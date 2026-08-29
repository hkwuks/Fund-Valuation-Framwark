import { api } from './api';
import { FundData } from './types';

export const fundInfoUI = {
  init(container: HTMLDivElement) {
    container.innerHTML = `
      <div class="fund-info-container fade-in">
        <!-- 标题和搜索卡片 -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">
              <span class="card-title-icon">📋</span>
              基金信息查询
            </h3>
          </div>
          <div class="card-body">
            <div class="fund-info-header">
              <div class="fund-search">
                <input type="text" id="fund-code-input" placeholder="请输入基金代码，例如：000001" />
                <button id="search-fund-btn" class="btn btn-primary">🔍 查询</button>
              </div>
            </div>
          </div>
        </div>

        <!-- 基金详情内容 -->
        <div id="fund-info-content" class="fund-info-content">
          <div class="empty-state">
            <div class="empty-state-icon">🔍</div>
            <h4 class="empty-state-title">请输入基金代码</h4>
            <p class="empty-state-description">在上方搜索框输入基金代码查询基金详细信息</p>
          </div>
        </div>
      </div>
    `;

    this.bindEvents();
  },

  bindEvents() {
    const searchBtn = document.getElementById('search-fund-btn');
    const fundCodeInput = document.getElementById('fund-code-input') as HTMLInputElement;

    if (searchBtn && fundCodeInput) {
      searchBtn.addEventListener('click', () => {
        const fundCode = fundCodeInput.value.trim();
        if (fundCode) {
          this.loadFundData(fundCode);
        }
      });

      fundCodeInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          const fundCode = fundCodeInput.value.trim();
          if (fundCode) {
            this.loadFundData(fundCode);
          }
        }
      });

      fundCodeInput.addEventListener('input', () => {
        // 实时搜索，可选功能
      });
    }
  },

  async loadFundData(fundCode: string) {
    const contentDiv = document.getElementById('fund-info-content');
    if (!contentDiv) return;

    // 显示加载状态
    contentDiv.innerHTML = `
      <div class="loading-state">
        <div class="loading-spinner"></div>
        <p class="loading-text">正在加载基金信息，请稍候...</p>
      </div>
    `;

    try {
      // 并行请求所有数据
      const [fundData, holdings, navHistory] = await Promise.all([
        api.getFundData(fundCode),
        api.getFundHoldings(fundCode),
        api.getFundNavHistory(fundCode)
      ]);

      if (!fundData) {
        contentDiv.innerHTML = `
          <div class="error-state">
            <div class="empty-state-icon">⚠️</div>
            <h4 class="empty-state-title">未找到基金信息</h4>
            <p class="empty-state-description">请检查基金代码是否正确</p>
          </div>
        `;
        return;
      }

      // 渲染基金信息
      this.renderFundInfo(fundData, holdings.data, navHistory.data);
    } catch (error) {
      console.error('加载基金信息失败', error);
      contentDiv.innerHTML = `
        <div class="error-state">
          <div class="empty-state-icon">⚠️</div>
          <h4 class="empty-state-title">加载失败</h4>
          <p class="empty-state-description">加载基金信息失败，请稍后重试</p>
        </div>
      `;
    }
  },

  renderFundInfo(fundData: FundData, holdings: any[], navHistory: any) {
    const contentDiv = document.getElementById('fund-info-content');
    if (!contentDiv) return;

    contentDiv.innerHTML = `
      <div class="fund-details scale-in">
        <div class="fund-header">
          <div>
            <h3>${fundData.fund_name}</h3>
            <p style="color: var(--text-tertiary); font-size: 14px; margin-top: 4px;">🏷️ ${fundData.fund_code}</p>
          </div>
          <div class="badge badge-gradient">${fundData.fund_type}</div>
        </div>

        <!-- 基金基本信息 -->
        <div class="fund-basic-info">
          <h4>📊 基本信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">基金类型</span>
              <span class="value">${fundData.fund_type}</span>
            </div>
            <div class="info-item">
              <span class="label">最新净值</span>
              <span class="value">${fundData.nav ? fundData.nav.toFixed(4) : '暂无数据'}</span>
            </div>
            <div class="info-item">
              <span class="label">净值日期</span>
              <span class="value">${fundData.nav_date || '暂无数据'}</span>
            </div>
            <div class="info-item">
              <span class="label">前一日净值</span>
              <span class="value">${fundData.previous_nav ? fundData.previous_nav.toFixed(4) : '暂无数据'}</span>
            </div>
            <div class="info-item">
              <span class="label">市场类型</span>
              <span class="value">${this.getMarketTypeText(fundData.market_type)}</span>
            </div>
            <div class="info-item">
              <span class="label">成立日期</span>
              <span class="value">${fundData.establish_date || '暂无数据'}</span>
            </div>
            ${fundData.tracking_index ? `
            <div class="info-item">
              <span class="label">跟踪指数</span>
              <span class="value">${fundData.tracking_index}</span>
            </div>
            ` : ''}
            ${fundData.benchmark ? `
            <div class="info-item">
              <span class="label">业绩比较基准</span>
              <span class="value">${fundData.benchmark}</span>
            </div>
            ` : ''}
          </div>
        </div>

        <!-- 基金持仓结构 -->
        <div class="fund-holdings">
          <h4>💹 持仓结构</h4>
          ${holdings && holdings.length > 0 ? this.renderHoldings(holdings) : '<p class="no-data">📭 暂无持仓数据</p>'}
        </div>

        <!-- 基金净值历史 -->
        <div class="fund-nav-history">
          <h4>📈 净值历史</h4>
          ${navHistory ? this.renderNavHistory(navHistory) : '<p class="no-data">📭 暂无净值历史数据</p>'}
        </div>
      </div>
    `;
  },

  getMarketTypeText(marketType: string): string {
    const marketTypeMap: Record<string, string> = {
      'on_exchange': '场内',
      'off_exchange': '场外',
      'unknown': '未知'
    };
    return marketTypeMap[marketType] || marketType;
  },

  renderHoldings(holdings: any[]): string {
    return `
      <div class="holdings-table-container">
        <table class="holdings-table">
          <thead>
            <tr>
              <th>资产代码</th>
              <th>资产名称</th>
              <th>资产类型</th>
              <th>持仓数量</th>
              <th>持仓市值</th>
              <th>持仓占比</th>
            </tr>
          </thead>
          <tbody>
            ${holdings.map(holding => `
              <tr>
                <td><strong>${holding.asset_code}</strong></td>
                <td>${holding.asset_name}</td>
                <td><span class="badge badge-secondary">${this.getAssetTypeText(holding.asset_type)}</span></td>
                <td>${holding.quantity.toFixed(2)}</td>
                <td>${holding.market_value ? holding.market_value.toFixed(2) : '0.00'}</td>
                <td><span class="badge badge-primary">${holding.weight ? (holding.weight * 100).toFixed(2) + '%' : '0.00%'}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
      <div class="holdings-chart" id="holdings-chart">
        <div class="empty-state" style="padding: 30px;">
          <p style="color: var(--text-tertiary);">📊 持仓分布图表待集成</p>
        </div>
      </div>
    `;
  },

  getAssetTypeText(assetType: string): string {
    const assetTypeMap: Record<string, string> = {
      'stock': '股票',
      'fund': '基金',
      'index': '指数',
      'bond': '债券'
    };
    return assetTypeMap[assetType] || assetType;
  },

  renderNavHistory(navHistory: any): string {
    if (!navHistory.data || navHistory.data.length === 0) {
      return '<p class="no-data">📭 暂无净值历史数据</p>';
    }

    // 获取前 10 条数据
    const recentData = navHistory.data.slice(0, 10);

    // 计算简单趋势
    const trendData = recentData.map((item: any) => {
      const trend = item.change_percent >= 0 ? '📈' : '📉';
      const changeClass = item.change_percent >= 0 ? 'positive' : 'negative';
      return `
        <tr>
          <td>${item.date}</td>
          <td><strong>${item.nav.toFixed(4)}</strong></td>
          <td>${item.cumulative_nav.toFixed(4)}</td>
          <td class="${changeClass}">${trend} ${item.change_percent >= 0 ? '+' : ''}${item.change_percent.toFixed(2)}%</td>
        </tr>
      `;
    }).join('');

    return `
      <div class="nav-history-chart" id="nav-history-chart">
        <div class="empty-state" style="padding: 30px;">
          <p style="color: var(--text-tertiary);">📈 净值走势图待集成</p>
        </div>
      </div>
      <div class="nav-history-table-container">
        <table class="nav-history-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>单位净值</th>
              <th>累计净值</th>
              <th>日涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            ${trendData}
          </tbody>
        </table>
      </div>
    `;
  },

  // 初始化图表（如果需要）
  initCharts() {
    // 这里可以集成图表库，如 Chart.js 等
    // 例如：初始化持仓结构饼图和净值历史折线图
  }
};
