import type { Fund, ValuationResult } from './types';
import { api } from './api';
import { StorageService } from './storage';

export class FundManager {
  private funds: Fund[] = [];
  private valuations: Map<string, ValuationResult> = new Map();

  // 初始化时加载基金数据
  async init(): Promise<void> {
    await this.loadFunds();
  }

  // 从后端和本地存储加载基金数据
  async loadFunds(): Promise<void> {
    try {
      // 优先从后端文件加载
      const fundsFromFile = await StorageService.loadFundsFromFile();
      console.log('从后端加载的基金数据:', fundsFromFile.length, '只基金');

      // 只有当后端数据非空时才覆盖本地数据，防止数据丢失
      if (fundsFromFile.length > 0) {
        this.funds = fundsFromFile;
        // 补齐历史持仓缺失的交易属性；已有人工维护字段不覆盖。
        const incomplete = this.funds.filter(f => !f.market_type || !f.trade_mode || !f.trading_profile_source);
        if (incomplete.length) {
          const enriched = await Promise.all(incomplete.map(async fund => {
            try {
              const profile = await api.getFundTradingProfile(fund.fund_code, fund.purchase_channel);
              const merged = { ...fund };
              for (const key of ['market_type', 'trade_mode', 'subscription_confirm_days',
                'redemption_confirm_days', 'cash_arrival_days', 'trading_profile_source',
                'trading_profile_confidence', 'trading_profile_needs_confirmation'] as const) {
                if (merged[key] == null && profile[key] != null) merged[key] = profile[key] as never;
              }
              return merged;
            } catch {
              return fund;
            }
          }));
          const changed = enriched.filter((fund, i) => JSON.stringify(fund) !== JSON.stringify(incomplete[i]));
          if (changed.length) {
            await Promise.all(changed.map(fund => api.updateFund(fund.fund_code, fund)));
            const updates = new Map(changed.map(fund => [fund.fund_code, fund]));
            this.funds = this.funds.map(fund => updates.get(fund.fund_code) || fund);
          }
        }
        // 同步到本地存储
        StorageService.saveFunds(this.funds);
        console.log('已从后端同步数据到本地存储');
      } else {
        // 后端数据为空时，从本地存储加载
        const localFunds = StorageService.loadFunds();
        console.log('后端数据为空，从本地存储加载:', localFunds.length, '只基金');
        this.funds = localFunds;
      }
    } catch (error) {
      console.error('加载基金数据失败:', error);
      // 加载失败时从本地存储加载
      const localFunds = StorageService.loadFunds();
      console.log('从本地存储加载:', localFunds.length, '只基金');
      this.funds = localFunds;
    }
  }

  // 获取所有基金
  getFunds(): Fund[] {
    return this.funds;
  }

  // 根据基金代码获取基金
  getFund(code: string): Fund | undefined {
    return this.funds.find(f => f.fund_code === code);
  }

  // 添加基金
  async addFund(fund: Fund): Promise<boolean> {
    try {
      console.log('开始添加基金:', fund);

      // 检查基金是否已存在
      if (this.getFund(fund.fund_code)) {
        console.error('基金已存在:', fund.fund_code);
        return false;
      }

      // 调用后端 API 添加基金
      const addResult = await api.addFund(fund);
      console.log('后端添加基金结果:', addResult);

      if (!addResult.success) {
        console.error('后端添加基金失败:', addResult.message);
        return false;
      }

      // 添加基金到本地数组
      this.funds.push(fund);
      console.log('基金已添加到本地数组');

      // 保存到本地存储
      StorageService.saveFunds(this.funds);
      console.log('基金已保存到本地存储');

      console.log('基金添加成功');
      return true;
    } catch (error) {
      console.error('添加基金失败:', error);
      return false;
    }
  }

  // 删除基金
  async deleteFund(code: string): Promise<boolean> {
    try {
      // 检查基金是否存在
      if (!this.getFund(code)) {
        console.error('基金不存在:', code);
        return false;
      }

      // 调用后端 API 删除基金
      const deleteResult = await api.deleteFund(code);
      console.log('后端删除基金结果:', deleteResult);

      if (!deleteResult.success) {
        console.error('后端删除基金失败:', deleteResult.message);
        return false;
      }

      // 从本地数组中删除
      this.funds = this.funds.filter(f => f.fund_code !== code);

      // 保存到本地存储
      StorageService.saveFunds(this.funds);

      // 清除估值数据
      this.valuations.delete(code);

      return true;
    } catch (error) {
      console.error('删除基金失败:', error);
      return false;
    }
  }

  // 计算估值（流式版本）
  async calculateValuation(codes: string[], preferHoldings: boolean = true): Promise<void> {
    try {
      await api.getFundValuationBatchStream(
        codes,
        {
          onValuation: (result) => {
            this.valuations.set(result.fund_code, result);

            const fund = this.getFund(result.fund_code);
            if (fund) {
              fund.estimated_nav = result.estimated_nav;
              fund.estimated_change_percent = result.estimated_change_percent;
              fund.confidence_note = result.confidence_note;
              fund.last_update = result.timestamp;
            }
          },
        },
        preferHoldings
      );
    } catch (error) {
      console.error('计算估值失败:', error);
    }
  }

  // 获取估值
  getValuation(code: string): ValuationResult | undefined {
    return this.valuations.get(code);
  }

  // 获取所有估值
  getAllValuations(): ValuationResult[] {
    return Array.from(this.valuations.values());
  }

  // 计算持仓权重
  calculateHoldingWeight(fund: Fund): Fund {
    const totalValue = fund.holdings.reduce((sum, h) => sum + (h.market_value || 0), 0);

    fund.holdings = fund.holdings.map(h => ({
      ...h,
      weight: totalValue > 0 ? ((h.market_value || 0) / totalValue * 100) : 0
    }));

    return fund;
  }
}

export const fundManager = new FundManager();
