import { fundManager } from './fundManager';
import { api } from './api';
import { toast } from './toast';
import { StorageService } from './storage';
import type { Fund } from './types';
import * as echarts from 'echarts';

class FundManagerUI {
  private container: HTMLElement | null = null;
  private refreshInterval: number | null = null;
  private refreshIntervalMs: number = 60000;
  private sortDirection: 'asc' | 'desc' | null = null;
  private sortColumn: 'change_percent' | 'pe_percentile' = 'change_percent';
  private isInitialized = false;
  private static readonly REFRESH_INTERVAL_OPTIONS = [
    { value: 30000, label: '30 秒' },
    { value: 60000, label: '1 分钟' },
    { value: 120000, label: '2 分钟' },
    { value: 300000, label: '5 分钟' },
    { value: 600000, label: '10 分钟' }
  ];

  async init(container: HTMLElement): Promise<void> {
    if (this.isInitialized) return;

    this.container = container;
    this.refreshIntervalMs = StorageService.loadRefreshInterval();

    await fundManager.init();
    await this.render();
    this.bindEvents();

    // 开始刷新估值（实时更新 UI）
    await this.refreshValuations();

    this.startAutoRefresh();
    this.isInitialized = true;
  }

  async render(): Promise<void> {
    if (!this.container) return;

    const funds = this.getSortedFunds();
    const totalValue = this.calculateTotalValue(funds);
    const totalProfit = this.calculateTotalProfit(funds);

    this.container.innerHTML = `
      <div class="fund-manager fade-in">
        <!-- 添加基金卡片 -->
        <div class="card fund-form">
          <div class="card-header">
            <h3 class="card-title">
              <span class="card-title-icon">➕</span>
              添加基金
            </h3>
          </div>
          <div class="card-body">
            <form id="add-fund-form">
              <div class="form-group">
                <label for="fund-code">基金代码</label>
                <div style="display: flex; gap: 8px;">
                  <input type="text" id="fund-code" required style="flex: 1;" placeholder="例如：000001" />
                  <button type="button" id="query-fund-btn" class="btn btn-secondary">查询</button>
                </div>
              </div>
              <div class="form-group">
                <label for="fund-name">基金名称</label>
                <input type="text" id="fund-name" required placeholder="自动填充或手动输入" />
              </div>
              <div class="form-group">
                <label for="fund-type">基金类型</label>
                <input type="text" id="fund-type" required placeholder="例如：股票型、混合型" />
              </div>
              <div class="form-group">
                <label for="total-shares">持有份额</label>
                <input type="number" id="total-shares" step="0.001" value="1" required />
              </div>
              <div class="form-group">
                <label for="market-type">市场类型</label>
                <select id="market-type" required>
                  <option value="">请选择</option>
                  <option value="on_exchange">场内</option>
                  <option value="off_exchange">场外</option>
                </select>
              </div>
              <div class="form-group">
                <label for="trade-mode">交易时序</label>
                <select id="trade-mode" required>
                  <option value="">请选择</option>
                  <option value="t0">T+0</option>
                  <option value="t1">T+1</option>
                  <option value="t2">T+2</option>
                </select>
              </div>
              <div class="form-group">
                <label for="subscription-confirm-days">申购确认日</label>
                <input type="number" id="subscription-confirm-days" min="0" required placeholder="交易日数" />
              </div>
              <div class="form-group">
                <label for="redemption-confirm-days">赎回确认日</label>
                <input type="number" id="redemption-confirm-days" min="0" required placeholder="交易日数" />
              </div>
              <div class="form-group">
                <label for="cash-arrival-days">资金到账日</label>
                <input type="number" id="cash-arrival-days" min="0" required placeholder="交易日数" />
              </div>
              <button type="submit" class="btn btn-primary btn-lg">
                <span>➕</span> 添加基金
              </button>
            </form>
          </div>
        </div>

        <!-- 基金列表卡片 -->
        <div class="card fund-list">
          <div class="fund-list-header">
            <h3>
              <span class="card-title-icon">📦</span>
              基金列表
              <span class="refresh-info">· 每${this.formatRefreshInterval(this.refreshIntervalMs)}自动刷新</span>
            </h3>
            <div class="fund-list-controls">
              <select id="refresh-interval-select" class="refresh-interval-select">
                ${FundManagerUI.REFRESH_INTERVAL_OPTIONS.map(option => `
                  <option value="${option.value}" ${this.refreshIntervalMs === option.value ? 'selected' : ''}>${option.label}</option>
                `).join('')}
              </select>
              <button type="button" id="refresh-all-btn" class="refresh-btn btn btn-primary">
                🔄 刷新数据
              </button>
            </div>
          </div>

          ${funds.length > 0 ? `
            <!-- 资产概览 -->
            <div class="valuation-body mb-3" style="margin-top: 20px;">
              <div class="valuation-item">
                <span class="label">持有基金数</span>
                <span class="value">${funds.length} 只</span>
              </div>
              <div class="valuation-item">
                <span class="label">持仓总份额</span>
                <span class="value">${this.formatNumber(funds.reduce((sum, f) => sum + f.total_shares, 0))}</span>
              </div>
              <div class="valuation-item">
                <span class="label">预估总市值</span>
                <span class="value">${totalValue !== '-' ? totalValue : '-'}</span>
              </div>
              <div class="valuation-item">
                <span class="label">日盈亏估算</span>
                <span class="value ${totalProfit !== '-' && totalProfit !== '0.00' ? (parseFloat(totalProfit.replace(',', '')) >= 0 ? 'positive' : 'negative') : ''}">
                  ${totalProfit !== '-' ? (parseFloat(totalProfit.replace(',', '')) >= 0 ? '+' : '') + totalProfit : '-'}
                </span>
              </div>
            </div>

            <div class="table-container mt-3">
              <table>
                <thead>
                  <tr>
                    <th>基金代码</th>
                    <th>基金名称</th>
                    <th>市场</th>
                    <th>基金类型</th>
                    <th>持有份额</th>
                    <th>最新净值<span class="nav-date" style="font-weight: normal; margin-left: 4px;">(日期)</span></th>
                    <th>前一日净值</th>
                    <th>预估净值</th>
                    <th class="sortable-header" id="sort-change-percent" style="cursor: pointer; user-select: none;">
                      预估涨跌幅
                      <span class="sort-icons">
                        <span class="sort-icon ${this.sortDirection === 'asc' ? 'active' : ''}">▲</span>
                        <span class="sort-icon ${this.sortDirection === 'desc' ? 'active' : ''}">▼</span>
                      </span>
                    </th>
                    <th>估值方法</th>
                    <th>折溢价</th>
                    <th class="sortable-header" id="sort-pe-percentile" style="cursor: pointer; user-select: none;">
                      估值分位
                      <span class="sort-icons">
                        <span class="sort-icon ${this.sortDirection === 'asc' ? 'active' : ''}">▲</span>
                        <span class="sort-icon ${this.sortDirection === 'desc' ? 'active' : ''}">▼</span>
                      </span>
                    </th>
                    <th>定投信号</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="fund-table-body">
                  ${funds.map(fund => this.renderFundRow(fund)).join('')}
                </tbody>
              </table>
            </div>
          ` : `
            <div class="empty-state mt-4">
              <div class="empty-state-icon">📭</div>
              <h4 class="empty-state-title">暂无基金数据</h4>
              <p class="empty-state-description">请在上方添加基金开始跟踪估值</p>
            </div>
          `}
        </div>
      </div>
    `;
  }

  private renderFundRow(fund: Fund): string {
    const changePercentDisplay = fund.estimated_change_percent != null
      ? (fund.estimated_change_percent >= 0 ? '+' : '') + fund.estimated_change_percent.toFixed(2) + '%'
      : '-';
    const changePercentTitle = fund.estimated_change_percent != null
      ? '预估涨跌幅'
      : (fund.confidence_note || '暂无预估涨跌幅数据');
    const navDisplay = fund.nav ? fund.nav.toFixed(4) : '-';
    const navDateDisplay = fund.nav_date ? `<span class="nav-date">(${fund.nav_date})</span>` : '';
    const previousNavDisplay = fund.previous_nav ? fund.previous_nav.toFixed(4) : '-';
    const estimatedNavDisplay = fund.estimated_nav ? fund.estimated_nav.toFixed(4) : '-';
    const positiveClass = fund.estimated_change_percent != null && fund.estimated_change_percent >= 0 ? 'positive' : 'negative';
    const valuationMethodDisplay = fund.valuation_method || '-';

    // 估值分位显示
    const pePercentileDisplay = fund.pe_percentile != null
      ? this.renderPercentileBadge(fund.pe_percentile, fund.pe_value, fund.index_name)
      : this.renderPercentileNa();
    const pePercentileTitle = fund.pe_percentile != null
      ? `${fund.index_name || '指数'} 当前 PE ${fund.pe_value != null ? fund.pe_value.toFixed(2) : '--'}，历史分位 ${fund.pe_percentile.toFixed(1)}%`
      : this.getPercentileNaTitle(fund);

    // 市场类型（场内/场外）
    const marketType = this.getMarketType(fund);
    const marketBadge = marketType === 'on_exchange'
      ? `<span class="badge badge-info">场内</span>`
      : marketType === 'off_exchange'
        ? `<span class="badge badge-secondary">场外</span>`
        : `<span class="badge badge-secondary">未知</span>`;

    // 场内折溢价
    const premiumDisplay = fund.premium_percent != null
      ? (fund.premium_percent > 0.5
          ? `<span class="premium-tag premium-positive" title="市价高于净值，溢价买入需谨慎">${fund.premium_percent.toFixed(2)}% 溢价</span>`
          : fund.premium_percent < -0.5
            ? `<span class="premium-tag premium-negative" title="市价低于净值，折价买入更划算">${fund.premium_percent.toFixed(2)}% 折价</span>`
            : `<span class="premium-tag premium-flat" title="市价与净值接近">${fund.premium_percent.toFixed(2)}%</span>`)
      : (marketType === 'on_exchange' ? '<span style="opacity:0.4">--</span>' : '<span style="opacity:0.3">-</span>');

    return `
      <tr class="fade-in" data-fund-code="${fund.fund_code}">
        <td><strong>${fund.fund_code}</strong></td>
        <td>${fund.fund_name}</td>
        <td>${marketBadge}</td>
        <td><span class="badge badge-secondary">${fund.fund_type}</span></td>
        <td>${this.formatNumber(fund.total_shares)}</td>
        <td>${navDisplay} ${navDateDisplay}</td>
        <td>${previousNavDisplay}</td>
        <td>${estimatedNavDisplay}</td>
        <td class="${positiveClass}" title="${changePercentTitle}">
          ${changePercentDisplay}
        </td>
        <td><span class="valuation-method-tag">${valuationMethodDisplay}</span></td>
        <td>${premiumDisplay}</td>
        <td title="${pePercentileTitle}">${pePercentileDisplay}</td>
        <td>${this.renderSignal(fund)}</td>
        <td>
          <button class="btn btn-danger btn-sm delete-fund" data-code="${fund.fund_code}">
            删除
          </button>
        </td>
      </tr>
    `;
  }

  // 市场类型判定（与后端 determine_market_type 逻辑一致）
  private getMarketType(fund: Fund): 'on_exchange' | 'off_exchange' | 'unknown' {
    const code = fund.fund_code || '';
    const name = fund.fund_name || '';
    const type = fund.fund_type || '';
    if (!code) return 'unknown';
    if (name.includes('联接')) return 'off_exchange';
    if (/ETF|LOF|封闭式/.test(name)) return 'on_exchange';
    if (/ETF|LOF|封闭式/.test(type)) return 'on_exchange';
    const p2 = code.slice(0, 2);
    if (['15', '16', '18'].includes(p2)) return 'on_exchange';
    if (p2 === '51') {
      return ['510', '511', '512', '513', '515', '516', '517', '518'].includes(code.slice(0, 3)) ? 'on_exchange' : 'off_exchange';
    }
    if (p2 === '50' || p2 === '52') return 'on_exchange';
    if (code.startsWith('0')) return 'off_exchange';
    return 'unknown';
  }

  // 定投信号显示
  private renderSignal(fund: Fund): string {
    const signal = fund.valuation_signal;
    if (!signal) {
      return `<span class="badge badge-secondary" style="font-size: 11px; opacity: 0.7;">无信号</span>`;
    }
    const colorMap: Record<string, string> = {
      '深度低估': 'badge-success',
      '低估': 'badge-success',
      '合理': 'badge-info',
      '偏高': 'badge-warning',
      '高估': 'badge-danger',
    };
    const colorClass = colorMap[signal] || 'badge-secondary';
    const action = fund.signal_action || '';
    const source = fund.signal_source || '';
    const sourceLabel = source === 'bond_yield' ? '基于债券收益率'
      : source === 'sp500_pe' ? '基于标普500 PE分位'
      : source === 'sp500_pe_proxy' ? '以标普500PE近似'
      : source === 'hsi_pe' ? '基于恒生指数PE分位'
      : source === 'overseas_price' ? '基于海外价格分位'
      : source === 'gold_price' ? '基于金价分位'
      : source === 'pe_lg' ? '基于历史PE分位'
      : source === 'pe_csindex' ? '基于中证官网PE分位'
      : source === 'pe_csindex_short' ? '基于短周期PE(仅20日)'
      : '';

    // 场内基金高估 → 加做空提示线（融券门槛高，仅作提示）
    const isOnExchange = this.getMarketType(fund) === 'on_exchange';
    const shortHint = (isOnExchange && (signal === '高估' || signal === '偏高'))
      ? `<span style="font-size: 10px; color: #e74c3c; font-weight: 600;" title="场内基金可融券做空（需两融账户且标的为两融标的）">🛑 可融券做空</span>`
      : '';

    return `
      <div style="display: flex; flex-direction: column; gap: 2px;">
        <span class="badge ${colorClass}" style="font-size: 11px;" title="${sourceLabel}">${signal}</span>
        <span style="font-size: 10px; color: var(--muted-color, #888);">${action}</span>
        ${shortHint}
      </div>
    `;
  }

  private renderPercentileBadge(percentile: number, peValue?: number, indexName?: string): string {
    // 分位越低越好（低估），分位越高越贵（高估）
    let colorClass = 'badge-success';
    if (percentile > 70) {
      colorClass = 'badge-danger';  // 高估
    } else if (percentile > 50) {
      colorClass = 'badge-warning';  // 偏高
    } else if (percentile > 30) {
      colorClass = 'badge-info';  // 适中
    }
    const label = percentile <= 30 ? '低估' : percentile <= 50 ? '适中' : percentile <= 70 ? '偏高' : '高估';
    return `<span class="badge ${colorClass}" style="font-size: 11px;" title="${indexName || ''} PE ${peValue != null ? peValue.toFixed(2) : ''}">${label} ${percentile.toFixed(0)}%</span>`;
  }

  // 无法计算估值分位时显示原因
  private getPercentileNaReason(fund: Fund): string {
    const t = fund.fund_type || '';
    const n = fund.fund_name || '';
    if (t.includes('债券') || t.includes('固收') || t.includes('货币')) return '债券基金无 PE 分位';
    if (t.includes('海外') || t.includes('QDII')) return '海外基金暂不支持 PE 分位';
    if (n.includes('黄金') || t.includes('商品')) return '黄金/商品基金无 PE 分位';
    if (t.includes('混合') || t.includes('主动')) return '主动基金无跟踪指数 PE 分位';
    return '暂无指数估值分位数据';
  }

  private renderPercentileNa(): string {
    return `<span class="badge badge-secondary" style="font-size: 11px; opacity: 0.7;">不适用</span>`;
  }

  private getPercentileNaTitle(fund: Fund): string {
    return this.getPercentileNaReason(fund);
  }

  private calculateTotalValue(funds: Fund[]): string {
    let total = 0;
    for (const fund of funds) {
      if (fund.estimated_nav && fund.total_shares) {
        total += fund.estimated_nav * fund.total_shares;
      } else if (fund.nav && fund.total_shares) {
        total += fund.nav * fund.total_shares;
      }
    }
    return total > 0 ? total.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '-';
  }

  private calculateTotalProfit(funds: Fund[]): string {
    let total = 0;
    for (const fund of funds) {
      if (fund.estimated_change_percent && fund.estimated_nav && fund.total_shares) {
        total += fund.estimated_nav * fund.total_shares * (fund.estimated_change_percent / 100);
      }
    }
    return total.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  private formatNumber(num: number): string {
    return num.toFixed(3).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  private bindEvents(): void {
    if (!this.container) return;

    // 使用事件委托
    this.container.addEventListener('submit', (e) => {
      const target = e.target as HTMLElement;
      if (target.id === 'add-fund-form') {
        e.preventDefault();
        this.handleAddFund(target as HTMLFormElement);
      }
    });

    this.container.addEventListener('click', (e) => {
      const target = e.target as HTMLElement;

      if (target.id === 'query-fund-btn') {
        e.preventDefault();
        this.handleQueryFund();
      }

      if (target.id === 'refresh-all-btn') {
        e.preventDefault();
        this.handleRefreshAll();
      }

      if (target.classList.contains('delete-fund')) {
        const fundCode = target.dataset.code;
        if (fundCode) {
          this.handleDeleteFund(fundCode);
        }
        return; // 删除按钮不触发行点击弹窗
      }

      if (target.id === 'sort-change-percent' || target.closest('#sort-change-percent')) {
        this.handleSortChangePercent();
        return;
      }

      if (target.id === 'sort-pe-percentile' || target.closest('#sort-pe-percentile')) {
        this.handleSortPePercentile();
        return;
      }

      // 点击基金行 → 弹出基金全量信息
      const row = target.closest<HTMLElement>('tr[data-fund-code]');
      if (row) {
        const fundCode = row.dataset.fundCode;
        if (fundCode) this.showFundDetail(fundCode);
      }
    });

    this.container.addEventListener('change', (e) => {
      const target = e.target as HTMLElement;

      if (target.id === 'refresh-interval-select') {
        this.handleRefreshIntervalChange(target as HTMLSelectElement);
      }
    });
  }

  async handleQueryFund(): Promise<void> {
    try {
      const fundCodeInput = this.container?.querySelector('#fund-code') as HTMLInputElement;
      const fundCode = fundCodeInput?.value.trim();

      if (!fundCode) {
        toast.warning('请输入基金代码');
        return;
      }

      const fundData = await api.getFundData(fundCode);
      if (!fundData) {
        toast.error('未查询到基金信息');
        return;
      }

      const fundNameInput = this.container?.querySelector('#fund-name') as HTMLInputElement;
      const fundTypeInput = this.container?.querySelector('#fund-type') as HTMLInputElement;

      if (fundNameInput && fundTypeInput) {
        fundNameInput.value = fundData.fund_name;
        fundTypeInput.value = fundData.fund_type;
        toast.success('基金信息查询成功，已自动填充到表单');
      }
    } catch (error) {
      console.error('查询基金信息失败:', error);
      toast.error('查询基金信息失败，请检查基金代码是否正确');
    }
  }

  async handleAddFund(form: HTMLFormElement): Promise<void> {
    try {
      const fundCode = (form.querySelector('#fund-code') as HTMLInputElement).value;
      const fundName = (form.querySelector('#fund-name') as HTMLInputElement).value;
      const fundType = (form.querySelector('#fund-type') as HTMLInputElement).value;
      const totalShares = parseFloat((form.querySelector('#total-shares') as HTMLInputElement).value);
      const marketType = (form.querySelector('#market-type') as HTMLSelectElement).value as 'on_exchange' | 'off_exchange';
      const tradeMode = (form.querySelector('#trade-mode') as HTMLSelectElement).value as 't0' | 't1' | 't2';
      const subscriptionConfirmDays = parseInt((form.querySelector('#subscription-confirm-days') as HTMLInputElement).value, 10);
      const redemptionConfirmDays = parseInt((form.querySelector('#redemption-confirm-days') as HTMLInputElement).value, 10);
      const cashArrivalDays = parseInt((form.querySelector('#cash-arrival-days') as HTMLInputElement).value, 10);

      const newFund: Fund = {
        fund_code: fundCode,
        fund_name: fundName,
        fund_type: fundType,
        total_shares: totalShares,
        market_type: marketType,
        trade_mode: tradeMode,
        subscription_confirm_days: subscriptionConfirmDays,
        redemption_confirm_days: redemptionConfirmDays,
        cash_arrival_days: cashArrivalDays,
        holdings: [],
      };

      // 先检查基金是否已在本地存在
      if (fundManager.getFund(fundCode)) {
        toast.error(`基金已存在：${fundCode}`);
        return;
      }

      // 调用后端 API 添加基金
      const addResult = await api.addFund(newFund);

      if (addResult.success) {
        // 暂停自动刷新，防止数据冲突
        this.stopAutoRefresh();

        // 保存现有基金的所有数据（包括基本数据和估值数据）
        const existingFunds = new Map<string, Fund>();
        const currentFunds = fundManager.getFunds();
        for (const fund of currentFunds) {
          existingFunds.set(fund.fund_code, { ...fund });
        }

        // 从后端重新加载基金列表（确保新基金在列表中）
        await fundManager.loadFunds();

        // 恢复现有基金的数据（保留之前的估值和基本信息）
        const updatedFunds = fundManager.getFunds();
        for (const fund of updatedFunds) {
          const existing = existingFunds.get(fund.fund_code);
          if (existing) {
            // 保留所有已有的数据
            fund.estimated_nav = existing.estimated_nav;
            fund.estimated_change_percent = existing.estimated_change_percent;
            fund.confidence_note = existing.confidence_note;
            fund.valuation_method = existing.valuation_method;
            fund.last_update = existing.last_update;
            fund.nav = existing.nav;
            fund.previous_nav = existing.previous_nav;
            fund.nav_date = existing.nav_date;
          }
        }

        toast.success('基金添加成功');
        form.reset();

        // 渲染表格
        await this.render();

        // 恢复自动刷新（新基金会在下次轮询时自动获得估值）
        this.startAutoRefresh();
      } else {
        // 显示后端返回的具体错误消息
        toast.error(addResult.message || '基金添加失败');
      }
    } catch (error) {
      console.error('处理添加基金时出错:', error);
      toast.error('添加基金时发生错误，请检查控制台');
    }
  }

  async handleDeleteFund(fundCode: string): Promise<void> {
    if (confirm(`确定要删除基金 ${fundCode} 吗？`)) {
      const success = await fundManager.deleteFund(fundCode);
      if (success) {
        toast.success('基金删除成功');
        await this.render();
      } else {
        toast.error('基金删除失败，可能是基金不存在或其他原因');
      }
    }
  }

  async handleRefreshAll(): Promise<void> {
    const funds = fundManager.getFunds();
    if (funds.length === 0) {
      toast.warning('暂无基金数据，请先添加基金');
      return;
    }

    const refreshBtn = this.container?.querySelector('#refresh-all-btn') as HTMLButtonElement;
    if (refreshBtn) {
      refreshBtn.disabled = true;
      refreshBtn.textContent = '🔄 刷新中...';
    }

    try {
      await this.refreshValuations();
      toast.success('数据刷新成功');
    } catch (error) {
      console.error('刷新数据失败:', error);
      toast.error('数据刷新失败，请稍后重试');
    } finally {
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 刷新数据';
      }
    }
  }

  async refreshValuations(): Promise<void> {
    const funds = fundManager.getFunds();
    if (funds.length === 0) return;

    try {
      const fundCodes = funds.map(f => f.fund_code);
      console.log('开始刷新估值，基金代码:', fundCodes);

      // 设置加载中标志

      // 显示加载状态
      this.showLoadingState();

      // 使用流式接口获取估值数据，实时更新 UI
      await api.getFundValuationBatchStream(
        fundCodes,
        {
          onValuation: async (result) => {
            const fund = fundManager.getFund(result.fund_code);
            if (fund) {
              fund.estimated_nav = result.estimated_nav;
              fund.estimated_change_percent = result.estimated_change_percent;
              fund.confidence_note = result.confidence_note;
              fund.valuation_method = result.valuation_method;
              fund.last_update = result.timestamp;
              if (result.latest_nav !== undefined && result.latest_nav !== null) {
                fund.nav = result.latest_nav;
              }
              if (result.previous_nav !== undefined && result.previous_nav !== null) {
                fund.previous_nav = result.previous_nav;
              }
              if (result.nav_date) {
                fund.nav_date = result.nav_date;
              }
              // 更新估值分位数据
              if (result.pe_percentile != null) {
                fund.pe_percentile = result.pe_percentile;
                fund.pe_value = result.pe_value;
                fund.pb_percentile = result.pb_percentile;
                fund.pb_value = result.pb_value;
                fund.index_code = result.index_code;
                fund.index_name = result.index_name;
              }
              // 更新定投信号
              if (result.valuation_signal) {
                fund.valuation_signal = result.valuation_signal;
                fund.signal_action = result.signal_action;
                fund.signal_source = result.signal_source;
              }
              // 更新场内折溢价
              if (result.premium_percent != null) {
                fund.premium_percent = result.premium_percent;
                fund.iopv = result.iopv;
              }
              // 实时更新单个基金行
              this.updateFundRow(result.fund_code);
            }
          },
          onError: (fundCode, message) => {
            console.error(`基金 ${fundCode} 估值失败：`, message);
            // 更新失败状态
            this.updateFundRowError(fundCode);
          },
          onComplete: (summary) => {
            console.log(`批量估值完成，成功：${summary.successCount}, 失败：${summary.failedCount}`);
            // 移除加载状态
            this.removeLoadingState();
            // 加载完成后，如果用户已选择排序方向，重新排序以正确显示
            if (this.sortDirection) {
              this.sortAndRender();
            }
          }
        },
        true
      );

      console.log('估值刷新完成');
    } catch (error) {
      console.error('刷新估值失败:', error);
      this.removeLoadingState();
    }
  }

  // 显示加载状态
  private showLoadingState(): void {
    if (!this.container) return;
    const tbody = this.container.querySelector('#fund-table-body');
    if (!tbody) return;

    const rows = tbody.querySelectorAll('tr');
    rows.forEach(row => {
      const fundCode = row.getAttribute('data-fund-code');
      if (fundCode) {
        // 添加加载中的视觉效果
        row.style.opacity = '0.5';
        row.setAttribute('data-loading', 'true');
      }
    });
  }

  // 移除加载状态
  private removeLoadingState(): void {
    if (!this.container) return;
    const tbody = this.container.querySelector('#fund-table-body');
    if (!tbody) return;

    const rows = tbody.querySelectorAll('tr');
    rows.forEach(row => {
      row.style.opacity = '1';
      row.removeAttribute('data-loading');
    });
  }

  // 更新单个基金行
  private updateFundRow(fundCode: string): void {
    if (!this.container) return;
    const tbody = this.container.querySelector('#fund-table-body');
    if (!tbody) return;

    const row = tbody.querySelector<HTMLElement>(`tr[data-fund-code="${fundCode}"]`);
    if (!row) return;

    const fund = fundManager.getFund(fundCode);
    if (!fund) return;

    // 重新渲染该行的内容
    row.outerHTML = this.renderFundRow(fund);
  }

  // 更新基金行错误状态
  private updateFundRowError(fundCode: string): void {
    if (!this.container) return;
    const tbody = this.container.querySelector('#fund-table-body');
    if (!tbody) return;

    const row = tbody.querySelector<HTMLElement>(`tr[data-fund-code="${fundCode}"]`);
    if (!row) return;

    // 添加错误视觉提示
    row.style.backgroundColor = 'rgba(255, 0, 0, 0.1)';
    row.setAttribute('data-error', 'true');
  }

  // 排序并重新渲染（在估值加载完成后调用）
  private sortAndRender(): void {
    if (!this.container) return;
    const tbody = this.container.querySelector('#fund-table-body');
    if (!tbody) return;

    const funds = this.getSortedFunds();
    tbody.innerHTML = funds.map(fund => this.renderFundRow(fund)).join('');
  }

  startAutoRefresh(): void {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
    }

    this.refreshInterval = window.setInterval(() => {
      this.refreshValuations(); // 自动刷新时不重新渲染整个表格
    }, this.refreshIntervalMs);
  }

  stopAutoRefresh(): void {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
      this.refreshInterval = null;
    }
  }

  formatRefreshInterval(ms: number): string {
    const seconds = ms / 1000;
    if (seconds < 60) {
      return `${Math.round(seconds)}秒`;
    } else if (seconds < 3600) {
      return `${Math.round(seconds / 60)}分钟`;
    } else {
      return `${Math.round(seconds / 3600)}小时`;
    }
  }

  async handleRefreshIntervalChange(select: HTMLSelectElement): Promise<void> {
    const newInterval = parseInt(select.value, 10);
    this.refreshIntervalMs = newInterval;
    StorageService.saveRefreshInterval(newInterval);
    this.stopAutoRefresh();
    this.startAutoRefresh();
    toast.success(`刷新周期已更新为${this.formatRefreshInterval(newInterval)}`);
    await this.render();
  }

  private getSortedFunds(): Fund[] {
    const funds = fundManager.getFunds();

    if (!this.sortDirection) {
      return funds;
    }

    return [...funds].sort((a, b) => {
      let aValue: number | null | undefined;
      let bValue: number | null | undefined;

      if (this.sortColumn === 'pe_percentile') {
        aValue = a.pe_percentile;
        bValue = b.pe_percentile;
      } else {
        aValue = a.estimated_change_percent;
        bValue = b.estimated_change_percent;
      }

      const aIsValid = aValue !== null && aValue !== undefined;
      const bIsValid = bValue !== null && bValue !== undefined;

      if (!aIsValid && !bIsValid) {
        return 0;
      }

      if (!aIsValid) {
        return this.sortDirection === 'asc' ? 1 : -1;
      }

      if (!bIsValid) {
        return this.sortDirection === 'asc' ? -1 : 1;
      }

      if (this.sortDirection === 'asc') {
        return (aValue as number) - (bValue as number);
      } else {
        return (bValue as number) - (aValue as number);
      }
    });
  }

  private handleSortChangePercent(): void {
    if (this.sortDirection === null || this.sortColumn !== 'change_percent') {
      this.sortDirection = 'desc';
    } else if (this.sortDirection === 'desc') {
      this.sortDirection = 'asc';
    } else {
      this.sortDirection = null;
    }
    this.sortColumn = 'change_percent';

    // 使用 sortAndRender 而不是 render，确保排序正确
    this.sortAndRender();
  }

  private handleSortPePercentile(): void {
    if (this.sortDirection === null || this.sortColumn !== 'pe_percentile') {
      this.sortDirection = 'asc';  // 分位低 = 低估 = 好，默认升序
    } else if (this.sortDirection === 'asc') {
      this.sortDirection = 'desc';
    } else {
      this.sortDirection = null;
    }
    this.sortColumn = 'pe_percentile';
    this.sortAndRender();
  }

  // ═══ 基金全量信息弹窗 ═══

  private async showFundDetail(fundCode: string): Promise<void> {
    const fund = fundManager.getFund(fundCode);
    if (!fund) return;

    // 创建遮罩和弹窗
    const overlay = document.createElement('div');
    overlay.className = 'fund-modal-overlay';
    overlay.innerHTML = `
      <div class="fund-modal">
        <div class="fund-modal-header">
          <h3>${fund.fund_name} <span style="font-weight:400;font-size:13px;color:var(--text-tertiary);">${fund.fund_code}</span></h3>
          <button class="fund-modal-close" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--text-secondary);">&times;</button>
        </div>
        <div class="fund-modal-body">
          <div class="fund-modal-loading">加载中...</div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    // 绑定关闭
    const close = () => overlay.remove();
    overlay.querySelector('.fund-modal-close')?.addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    // 加载数据
    try {
      const navRes = await api.getFundNavHistory(fundCode);
      const navData = navRes?.success && navRes?.data ? navRes.data : [];
      this.renderFundDetailModal(fund, navData, overlay);
    } catch (e) {
      overlay.querySelector('.fund-modal-body')!.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">加载失败</div>';
    }
  }

  private renderFundDetailModal(fund: Fund, navData: any[], overlay: HTMLElement): void {
    const body = overlay.querySelector('.fund-modal-body')!;

    // 基本信息
    const isDark = document.body.classList.contains('dark-mode');
    const signalColor = (s: string) => s === '深度低估' || s === '低估' ? 'var(--success-color)' : s === '高估' ? 'var(--danger-color)' : s === '偏高' ? 'var(--warning-color)' : 'var(--text-secondary)';
    const marketType = this.getMarketType(fund);
    const marketLabel = marketType === 'on_exchange' ? '场内' : marketType === 'off_exchange' ? '场外' : '未知';

    body.innerHTML = `
      <div class="fund-modal-info">
        <div class="info-grid">
          <div class="info-item"><span class="label">基金类型</span><span class="value">${fund.fund_type}</span></div>
          <div class="info-item"><span class="label">市场类型</span><span class="value">${marketLabel}</span></div>
          <div class="info-item"><span class="label">最新净值</span><span class="value">${fund.nav ? fund.nav.toFixed(4) : '-'}</span></div>
          <div class="info-item"><span class="label">净值日期</span><span class="value">${fund.nav_date || '-'}</span></div>
          <div class="info-item"><span class="label">预估涨跌</span><span class="value" style="color:${fund.estimated_change_percent && fund.estimated_change_percent >= 0 ? 'var(--danger-color)' : 'var(--success-color)'}">${fund.estimated_change_percent != null ? (fund.estimated_change_percent >= 0 ? '+' : '') + fund.estimated_change_percent.toFixed(2) + '%' : '-'}</span></div>
          ${fund.premium_percent != null ? `<div class="info-item"><span class="label">折溢价</span><span class="value" style="color:${fund.premium_percent > 0.5 ? 'var(--danger-color)' : fund.premium_percent < -0.5 ? 'var(--success-color)' : 'var(--text-secondary)'}">${fund.premium_percent.toFixed(2)}%</span></div>` : ''}
          <div class="info-item"><span class="label">持有份额</span><span class="value">${this.formatNumber(fund.total_shares)}</span></div>
        </div>
      </div>
      <div class="fund-modal-section">
        <h4>📈 净值走势</h4>
        <div class="fund-modal-chart"></div>
      </div>
      <div class="fund-modal-section">
        <h4>📊 估值信号</h4>
        <div class="fund-modal-signals">
          <div class="info-grid">
            ${fund.pe_percentile != null ? `
              <div class="info-item"><span class="label">估值分位</span><span class="value" style="color:${fund.pe_percentile > 70 ? 'var(--danger-color)' : fund.pe_percentile > 50 ? 'var(--warning-color)' : 'var(--success-color)'}">${fund.pe_percentile.toFixed(1)}%</span></div>
              <div class="info-item"><span class="label">PE</span><span class="value">${fund.pe_value != null ? fund.pe_value.toFixed(2) : '-'}</span></div>
              <div class="info-item"><span class="label">跟踪指数</span><span class="value">${fund.index_name || '-'}</span></div>
            ` : ''}
            ${fund.valuation_signal ? `
              <div class="info-item"><span class="label">定投信号</span><span class="value" style="font-weight:700;color:${signalColor(fund.valuation_signal)}">${fund.valuation_signal}</span></div>
              <div class="info-item"><span class="label">建议操作</span><span class="value">${fund.signal_action || '-'}</span></div>
            ` : ''}
          </div>
        </div>
      </div>`;

    // 渲染净值走势图
    const chartEl = body.querySelector<HTMLElement>('.fund-modal-chart');
    if (chartEl && navData.length > 1) {
      this.renderModalChart(chartEl, navData, isDark, fund.fund_name);
    } else if (chartEl) {
      chartEl.innerHTML = '<div style="padding:30px;text-align:center;color:var(--text-tertiary);font-size:13px;">暂无净值数据</div>';
    }
  }

  private renderModalChart(chartEl: HTMLElement, navData: any[], isDark: boolean, fundName: string): void {
    const dates = navData.map((d: any) => (d.date || '').slice(0, 10));
    const navValues = navData.map((d: any) => d.nav || d.adjusted_nav || 0);

    const chart = echarts.init(chartEl);
    const axColor = isDark ? '#8494ad' : '#64748b';
    const axLineColor = isDark ? '#1e2d42' : '#cbd5e1';
    const gridColor = isDark ? '#152238' : '#e2e8f0';
    const navColor = isDark ? '#4a90d9' : '#3b82f6';
    const navAreaColor = isDark ? 'rgba(74,144,217,0.2)' : 'rgba(59,130,246,0.12)';

    chart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      grid: { left: '3%', right: '4%', bottom: '8%', top: '4%', containLabel: true },
      xAxis: {
        type: 'category', data: dates,
        axisLabel: { rotate: 45, fontSize: 11, color: axColor },
        axisLine: { lineStyle: { color: axLineColor } },
        splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: axColor },
        splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
      },
      dataZoom: [{ type: 'inside', xAxisIndex: 0 }],
      series: [{
        type: 'line', name: fundName,
        data: navValues, smooth: true,
        lineStyle: { width: 2, color: navColor },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: navAreaColor },
          { offset: 1, color: isDark ? 'rgba(74,144,217,0)' : 'rgba(59,130,246,0)' },
        ]) },
      }],
    });
  }
}

export const fundManagerUI = new FundManagerUI();
