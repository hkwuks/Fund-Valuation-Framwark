import { api } from './api';
import { toast } from './toast';
import { StorageService } from './storage';
import type { MarketData } from './types';

const isDarkMode = () => document.body.classList.contains('dark-mode') || window.matchMedia('(prefers-color-scheme: dark)').matches;

// ===== localStorage 持久缓存 =====
const LOCAL_CACHE_PREFIX = 'fund_val_market_';
const LOCAL_CACHE_TTL_KEY = 'fund_val_market_cache_ttl';
const DEFAULT_LOCAL_TTL = 120_000; // 2 分钟

function loadPersistentCache(code: string): any | null {
  try {
    const raw = localStorage.getItem(LOCAL_CACHE_PREFIX + code);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (Date.now() - entry.ts < entry.ttl) return entry.data;
    localStorage.removeItem(LOCAL_CACHE_PREFIX + code);
  } catch { /* ignore corrupt cache */ }
  return null;
}

function savePersistentCache(code: string, data: any, ttl?: number) {
  try {
    const cacheTtl = ttl ?? parseInt(localStorage.getItem(LOCAL_CACHE_TTL_KEY) || String(DEFAULT_LOCAL_TTL), 10);
    localStorage.setItem(LOCAL_CACHE_PREFIX + code, JSON.stringify({ data, ts: Date.now(), ttl: cacheTtl }));
  } catch { /* quota exceeded — ignore */ }
}

// =====

interface CacheConfig {
  ttl: number; // 缓存时间（毫秒）
  lastUpdate: number;
  data: any;
}

class MarketDataUI {
  private container!: HTMLDivElement;
  private currentTab: string = 'indices';
  private previousTab: string = 'indices'; // 记录返回来源
  private marketData: Record<string, MarketData> = {};
  private supportedIndices: any = null;
  private isDataLoaded: boolean = false;

  // 前端缓存
  private cache: Map<string, CacheConfig> = new Map();
  private defaultCacheTtl: number;

  constructor() {
    // 从 localStorage 加载缓存时间设置
    this.defaultCacheTtl = StorageService.loadMarketCacheTtl();
  }

  // 分批加载相关
  private abortControllers: AbortController[] = [];
  private loadedCount: number = 0;
  private totalCount: number = 0;

  init(container: HTMLDivElement) {
    this.container = container;
    this.render();
    this.setupLazyLoading();
  }

  /** 当标签页被激活时调用（用于刷新后标签保持等场景） */
  onActivated() {
    if (!this.isDataLoaded) {
      this.loadSupportedIndices();
    }
  }

  private setupLazyLoading() {
    // 监听标签切换事件，当切换到市场数据标签页时加载数据
    const marketDataTabButton = document.querySelector<HTMLButtonElement>('.tab-button[data-tab="market-data"]');
    if (marketDataTabButton) {
      marketDataTabButton.addEventListener('click', () => {
        if (!this.isDataLoaded) {
          this.loadSupportedIndices();
        }
      });
    }

    // 检查当前是否已经在市场数据标签页（直接刷新页面的情况）
    const marketDataContainer = document.getElementById('market-data-container');
    if (marketDataContainer && marketDataContainer.classList.contains('active')) {
      if (!this.isDataLoaded) {
        this.loadSupportedIndices();
      }
    }
  }

  // 获取缓存数据
  private getCachedData(key: string): any | null {
    const cached = this.cache.get(key);
    if (cached) {
      const now = Date.now();
      if (now - cached.lastUpdate < cached.ttl) {
        return cached.data;
      }
    }
    return null;
  }

  // 设置缓存数据
  private setCachedData(key: string, data: any, ttl?: number) {
    this.cache.set(key, {
      ttl: ttl || this.defaultCacheTtl,
      lastUpdate: Date.now(),
      data
    });
  }

  private render() {
    this.container.innerHTML = `
      <div class="market-data-container fade-in">
        <!-- 市场数据标题卡片 -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">
              <span class="card-title-icon">🌍</span>
              市场数据
            </h3>
            <div class="cache-controls">
              <label class="cache-ttl-label">
                缓存时间:
                <select id="cache-ttl-select" class="cache-ttl-select">
                  <option value="30000" ${this.defaultCacheTtl === 30000 ? 'selected' : ''}>30秒</option>
                  <option value="60000" ${this.defaultCacheTtl === 60000 ? 'selected' : ''}>1分钟</option>
                  <option value="120000" ${this.defaultCacheTtl === 120000 ? 'selected' : ''}>2分钟</option>
                  <option value="300000" ${this.defaultCacheTtl === 300000 ? 'selected' : ''}>5分钟</option>
                </select>
              </label>
              <button id="refresh-cache-btn" class="btn btn-primary btn-sm">
                🔄 刷新
              </button>
            </div>
          </div>
          <div class="card-body">
            <div class="market-tabs">
              <button class="market-tab-button ${this.currentTab === 'indices' ? 'active' : ''}" data-tab="indices">
                📊 指数行情
              </button>
              <button class="market-tab-button ${this.currentTab === 'search' ? 'active' : ''}" data-tab="search">
                🔍 行情查询
              </button>
              <button class="market-tab-button ${this.currentTab === 'details' ? 'active' : ''}" data-tab="details">
                📈 数据详情
              </button>
            </div>

            <div class="market-tab-content">
              ${this.renderIndicesTab()}
              ${this.renderSearchTab()}
              ${this.renderDetailsTab()}
            </div>
          </div>
        </div>
      </div>
    `;

    this.bindEvents();
  }

  private renderIndicesTab() {
    return `
      <div class="indices-tab ${this.currentTab === 'indices' ? 'active' : ''}">
        <div class="indices-section">
          <h3 style="font-size: 16px; margin-bottom: 16px; color: var(--text-primary);">🇨🇳 国内指数</h3>
          <div id="domestic-indices" class="indices-grid">
            ${this.renderIndicesLoader()}
          </div>
        </div>

        <div class="indices-section">
          <h3 style="font-size: 16px; margin-bottom: 16px; color: var(--text-primary);">🌏 海外指数</h3>
          <div id="global-indices" class="indices-grid">
            ${this.renderIndicesLoader()}
          </div>
        </div>
      </div>
    `;
  }

  private renderSearchTab() {
    return `
      <div class="search-tab ${this.currentTab === 'search' ? 'active' : ''}">
        <div class="search-container">
          <div class="search-input-group">
            <select id="asset-type">
              <option value="stock">📈 股票</option>
              <option value="etf">💹 ETF</option>
              <option value="index">📊 指数</option>
              <option value="global-index">🌍 海外指数</option>
            </select>
            <input type="text" id="asset-code" placeholder="请输入代码，例如：000001" />
            <button id="search-button" class="btn btn-primary">🔍 查询</button>
          </div>
          <div id="search-result" class="search-result">
            <p>👈 请输入代码进行查询</p>
          </div>
        </div>
      </div>
    `;
  }

  private renderDetailsTab() {
    return `
      <div class="details-tab ${this.currentTab === 'details' ? 'active' : ''}">
        <div class="market-details-content">
          <h3 style="font-size: 18px; margin-bottom: 20px; color: var(--text-secondary);">📋 市场数据详情</h3>
          <div id="market-details" class="market-details">
            <div class="empty-state">
              <div class="empty-state-icon">📭</div>
              <h4 class="empty-state-title">暂无数据</h4>
              <p class="empty-state-description">请先在指数行情或搜索页面选择一个资产查看详情</p>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  private bindEvents() {
    const tabButtons = this.container.querySelectorAll<HTMLButtonElement>('.market-tab-button');
    tabButtons.forEach(button => {
      button.addEventListener('click', () => {
        this.currentTab = button.dataset.tab || 'indices';
        this.render();
        if (this.currentTab === 'indices') {
          this.loadSupportedIndices();
        }
      });
    });

    // 搜索按钮事件
    const searchButton = this.container.querySelector<HTMLButtonElement>('#search-button');
    if (searchButton) {
      searchButton.addEventListener('click', () => {
        this.handleSearch();
      });
    }

    // 刷新按钮事件
    const refreshCacheBtn = this.container.querySelector<HTMLButtonElement>('#refresh-cache-btn');
    if (refreshCacheBtn) {
      refreshCacheBtn.addEventListener('click', () => {
        this.handleRefresh();
      });
    }

    // 缓存时间选择事件
    const cacheTtlSelect = this.container.querySelector<HTMLSelectElement>('#cache-ttl-select');
    if (cacheTtlSelect) {
      cacheTtlSelect.addEventListener('change', () => {
        this.defaultCacheTtl = parseInt(cacheTtlSelect.value, 10);
        StorageService.saveMarketCacheTtl(this.defaultCacheTtl);
        toast.success(`缓存时间已设置为 ${this.formatTtl(this.defaultCacheTtl)}`);
      });
    }

    // 返回按钮事件
    const backButton = this.container.querySelector<HTMLButtonElement>('#back-to-indices');
    if (backButton) {
      backButton.addEventListener('click', () => {
        // 返回到之前的标签
        this.currentTab = this.previousTab;
        this.render();
        if (this.currentTab === 'indices') {
          this.loadSupportedIndices();
        }
      });
    }

    // 回车搜索
    const assetCodeInput = this.container.querySelector<HTMLInputElement>('#asset-code');
    if (assetCodeInput) {
      assetCodeInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          this.handleSearch();
        }
      });
    }
  }

  private formatTtl(ms: number): string {
    if (ms < 60000) {
      return `${ms / 1000}秒`;
    }
    return `${ms / 60000}分钟`;
  }

  // 处理刷新按钮点击
  private async handleRefresh() {
    // 清除前端缓存（内存 + localStorage）
    this.cache.clear();
    this.marketData = {};
    this.isDataLoaded = false;
    // 清除所有持久缓存
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key?.startsWith(LOCAL_CACHE_PREFIX)) localStorage.removeItem(key);
    }

    toast.success('正在刷新数据...');

    // 重新加载数据（强制刷新，跳过缓存检查）
    await this.loadSupportedIndices(true);
  }

  private async loadSupportedIndices(forceRefresh: boolean = false) {
    try {
      // 检查缓存
      if (!forceRefresh) {
        const cached = this.getCachedData('supportedIndices');
        if (cached) {
          console.log('使用缓存的指数列表:', cached);
          this.supportedIndices = cached;
          this.isDataLoaded = true;
          this.renderIndices(forceRefresh);
          return;
        }
      }

      console.log('正在请求指数列表...');
      const response = await api.getSupportedIndices();
      console.log('指数列表响应:', response);
      if (response && response.success) {
        this.supportedIndices = response.data;
        this.isDataLoaded = true;
        // 缓存数据
        this.setCachedData('supportedIndices', response.data);
        this.renderIndices(forceRefresh);
      } else {
        console.error('获取指数列表失败:', response);
      }
    } catch (error) {
      console.error('加载支持的指数列表失败', error);
    }
  }

  private async renderIndices(forceRefresh: boolean = false) {
    console.log('renderIndices 被调用, supportedIndices:', this.supportedIndices);
    if (!this.supportedIndices) {
      console.log('supportedIndices 为空，不渲染');
      return;
    }

    // 取消之前的请求
    this.cancelAllRequests();
    this.loadedCount = 0;

    const domesticIndicesContainer = this.container.querySelector('#domestic-indices');
    const globalIndicesContainer = this.container.querySelector('#global-indices');
    console.log('DOM 容器:', { domestic: domesticIndicesContainer, global: globalIndicesContainer });

    // 收集所有需要查询的指数代码
    const domesticCodes: string[] = [];
    const globalCodes: string[] = [];
    const domesticIndexMap: Record<string, string> = {};
    const globalIndexMap: Record<string, string> = {};

    if (domesticIndicesContainer) {
      domesticIndicesContainer.innerHTML = '';
      const domestic = this.supportedIndices.domestic;
      console.log('国内指数数据:', domestic);
      if (domestic && typeof domestic === 'object') {
        for (const [code, name] of Object.entries(domestic)) {
          domesticCodes.push(code);
          domesticIndexMap[code] = name as string;
        }
      }
    }

    if (globalIndicesContainer) {
      globalIndicesContainer.innerHTML = '';
      const global = this.supportedIndices.global;
      console.log('海外指数数据:', global);
      if (global && typeof global === 'object') {
        for (const [code, name] of Object.entries(global)) {
          globalCodes.push(code);
          globalIndexMap[code] = name as string;
        }
      }
    }

    this.totalCount = domesticCodes.length + globalCodes.length;
    console.log('收集到的代码:', { domesticCodes, globalCodes });
    console.log('指数名称映射:', { domesticIndexMap, globalIndexMap });

    // 并行加载国内和海外指数（SSE流式支持真正的并行）
    await Promise.all([
      this.loadDomesticIndicesBatched(domesticCodes, domesticIndexMap, domesticIndicesContainer, forceRefresh),
      this.loadGlobalIndicesBatched(globalCodes, globalIndexMap, globalIndicesContainer, forceRefresh)
    ]);
  }

  private cancelAllRequests() {
    for (const controller of this.abortControllers) {
      controller.abort();
    }
    this.abortControllers = [];
  }

  private updateLoadingProgress() {
    this.loadedCount++;
    const progress = Math.round((this.loadedCount / this.totalCount) * 100);
    console.log(`加载进度: ${this.loadedCount}/${this.totalCount} (${progress}%)`);
  }

  // 使用 SSE 流式加载国内指数
  private async loadDomesticIndicesBatched(
    codes: string[],
    indexMap: Record<string, string>,
    container: Element | null,
    forceRefresh = false
  ) {
    if (codes.length === 0 || !container) return;

    // 过滤出需要网络请求的代码（优先走 localStorage 持久缓存）
    const toFetch: string[] = [];
    const codeElements: Record<string, HTMLElement> = {};

    for (const code of codes) {
      const cached = !forceRefresh ? loadPersistentCache(code) : null;
      if (cached) {
        this.marketData[code] = cached;
        const card = document.createElement('div');
        card.className = 'index-card';
        card.innerHTML = this.renderIndexCard(cached);
        card.addEventListener('click', () => this.showMarketDetails(cached, 'indices'));
        container.appendChild(card);
        codeElements[code] = card;
        this.updateLoadingProgress();
      } else {
        toFetch.push(code);
        const el = document.createElement('div');
        el.className = 'index-card loading';
        el.innerHTML = `
          <h4>${indexMap[code]}</h4>
          <p class="code">${code}</p>
          <div class="loading-indicator">
            <div class="loading-spinner-small"></div>
            <span>加载中...</span>
          </div>
        `;
        container.appendChild(el);
        codeElements[code] = el;
        this.updateLoadingProgress();
      }
    }

    if (toFetch.length === 0) return;

    // 创建 AbortController 用于取消请求
    const controller = new AbortController();
    this.abortControllers.push(controller);

    // 使用 SSE 流式获取数据
    return new Promise<void>((resolve, reject) => {
      api.getIndexBatchStream(codes, {
        onIndex: (result) => {
          const { code, success, data } = result;
          const element = codeElements[code];
          if (!element) return;

          if (success && data) {
            this.marketData[code] = data;
            savePersistentCache(code, data);
            // 替换为成功卡片
            const newElement = document.createElement('div');
            newElement.className = 'index-card';
            newElement.innerHTML = this.renderIndexCard(data);
            newElement.addEventListener('click', () => {
              this.showMarketDetails(data, 'indices');
            });
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          } else {
            // 替换为错误卡片
            const newElement = document.createElement('div');
            newElement.className = 'index-card error';
            newElement.innerHTML = `
              <h4>${indexMap[code]}</h4>
              <p class="code">${code}</p>
              <p class="error-message">⚠️ 获取数据失败</p>
            `;
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          }
        },
        onProgress: (progress) => {
          console.log(`国内指数加载进度: ${progress.current}/${progress.total}, 成功: ${progress.successCount}, 失败: ${progress.failedCount}`);
        },
        onComplete: (summary) => {
          console.log(`国内指数加载完成: 总计 ${summary.total}, 成功: ${summary.successCount}, 失败: ${summary.failedCount}`);
          // 从列表中移除已完成的 controller
          const index = this.abortControllers.indexOf(controller);
          if (index > -1) {
            this.abortControllers.splice(index, 1);
          }
          resolve();
        },
        onError: (code, message) => {
          console.error(`国内指数 ${code} 加载失败:`, message);
          const element = codeElements[code];
          if (element) {
            const newElement = document.createElement('div');
            newElement.className = 'index-card error';
            newElement.innerHTML = `
              <h4>${indexMap[code]}</h4>
              <p class="code">${code}</p>
              <p class="error-message">⚠️ 获取数据失败</p>
            `;
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          }
        }
      }, controller.signal).catch(error => {
        console.error('SSE 流式加载国内指数失败:', error);
        // 从列表中移除已完成的 controller
        const index = this.abortControllers.indexOf(controller);
        if (index > -1) {
          this.abortControllers.splice(index, 1);
        }
        reject(error);
      });
    });
  }

  // 使用 SSE 流式加载海外指数
  private async loadGlobalIndicesBatched(
    codes: string[],
    indexMap: Record<string, string>,
    container: Element | null,
    forceRefresh = false
  ) {
    if (codes.length === 0 || !container) return;

    // 过滤出需要网络请求的代码（优先走 localStorage 持久缓存）
    const toFetch: string[] = [];
    const codeElements: Record<string, HTMLElement> = {};

    for (const code of codes) {
      const cached = !forceRefresh ? loadPersistentCache(code) : null;
      if (cached) {
        this.marketData[code] = cached;
        const card = document.createElement('div');
        card.className = 'index-card';
        card.innerHTML = this.renderIndexCard(cached);
        card.addEventListener('click', () => this.showMarketDetails(cached, 'indices'));
        container.appendChild(card);
        codeElements[code] = card;
        this.updateLoadingProgress();
      } else {
        toFetch.push(code);
        const el = document.createElement('div');
        el.className = 'index-card loading';
        el.innerHTML = `
          <h4>${indexMap[code]}</h4>
          <p class="code">${code}</p>
          <div class="loading-indicator">
            <div class="loading-spinner-small"></div>
            <span>加载中...</span>
          </div>
        `;
        container.appendChild(el);
        codeElements[code] = el;
        this.updateLoadingProgress();
      }
    }

    if (toFetch.length === 0) return;

    // 创建 AbortController 用于取消请求
    const controller = new AbortController();
    this.abortControllers.push(controller);

    // 使用 SSE 流式获取数据
    return new Promise<void>((resolve, reject) => {
      api.getGlobalIndexBatchStream(codes, {
        onIndex: (result) => {
          const { code, success, data } = result;
          const element = codeElements[code];
          if (!element) return;

          if (success && data) {
            this.marketData[code] = data;
            savePersistentCache(code, data);
            // 替换为成功卡片
            const newElement = document.createElement('div');
            newElement.className = 'index-card';
            newElement.innerHTML = this.renderIndexCard(data);
            newElement.addEventListener('click', () => {
              this.showMarketDetails(data, 'indices');
            });
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          } else {
            // 替换为错误卡片
            const newElement = document.createElement('div');
            newElement.className = 'index-card error';
            newElement.innerHTML = `
              <h4>${indexMap[code]}</h4>
              <p class="code">${code}</p>
              <p class="error-message">⚠️ 获取数据失败</p>
            `;
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          }
        },
        onProgress: (progress) => {
          console.log(`海外指数加载进度: ${progress.current}/${progress.total}, 成功: ${progress.successCount}, 失败: ${progress.failedCount}`);
        },
        onComplete: (summary) => {
          console.log(`海外指数加载完成: 总计 ${summary.total}, 成功: ${summary.successCount}, 失败: ${summary.failedCount}`);
          // 从列表中移除已完成的 controller
          const index = this.abortControllers.indexOf(controller);
          if (index > -1) {
            this.abortControllers.splice(index, 1);
          }
          resolve();
        },
        onError: (code, message) => {
          console.error(`海外指数 ${code} 加载失败:`, message);
          const element = codeElements[code];
          if (element) {
            const newElement = document.createElement('div');
            newElement.className = 'index-card error';
            newElement.innerHTML = `
              <h4>${indexMap[code]}</h4>
              <p class="code">${code}</p>
              <p class="error-message">⚠️ 获取数据失败</p>
            `;
            element.replaceWith(newElement);
            codeElements[code] = newElement;
          }
        }
      }, controller.signal).catch(error => {
        console.error('SSE 流式加载海外指数失败:', error);
        // 从列表中移除已完成的 controller
        const index = this.abortControllers.indexOf(controller);
        if (index > -1) {
          this.abortControllers.splice(index, 1);
        }
        reject(error);
      });
    });
  }

  private renderIndexCard(data: any) {
    const changeClass = data.change_percent && data.change_percent >= 0 ? 'positive' : 'negative';
    const changeIcon = data.change_percent && data.change_percent >= 0 ? '📈' : '📉';
    const price = typeof data.price === 'number' ? data.price.toFixed(2) : '-';
    const changePercent = typeof data.change_percent === 'number' ? data.change_percent.toFixed(2) + '%' : 'N/A';
    const change = typeof data.change === 'number' ? (data.change >= 0 ? '+' : '') + data.change.toFixed(2) : '';

    return `
        <h4>${data.name || '未知'}</h4>
        <p class="code">${data.code || ''}</p>
        <div class="price-info">
          <p class="price">${price}</p>
          <div class="change ${changeClass}">
            <span class="change-percent">${changeIcon} ${changePercent}</span>
            ${change ? `<span class="change-value">${change}</span>` : ''}
          </div>
        </div>
        <p class="timestamp">🕐 ${new Date().toLocaleString()}</p>
    `;
  }

  private async handleSearch() {
    const assetTypeSelect = this.container.querySelector<HTMLSelectElement>('#asset-type');
    const assetCodeInput = this.container.querySelector<HTMLInputElement>('#asset-code');
    const searchResultContainer = this.container.querySelector('#search-result');

    if (!assetTypeSelect || !assetCodeInput || !searchResultContainer) return;

    const assetType = assetTypeSelect.value;
    const assetCode = assetCodeInput.value.trim();

    if (!assetCode) {
      searchResultContainer.innerHTML = '<div class="error-message">⚠️ 请输入资产代码</div>';
      return;
    }

    searchResultContainer.innerHTML = `
      <div class="loading-state">
        <div class="loading-spinner"></div>
        <p class="loading-text">正在查询中...</p>
      </div>
    `;

    try {
      let response: any;
      let cacheKey = `${assetType}_${assetCode}`;

      // 检查缓存
      let marketData = this.getCachedData(cacheKey);

      if (!marketData) {
        switch (assetType) {
          case 'stock':
            response = await api.getStockPrice(assetCode);
            break;
          case 'etf':
            response = await api.getEtfPrice(assetCode);
            break;
          case 'index':
            response = await api.getIndexPrice(assetCode);
            break;
          case 'global-index':
            response = await api.getGlobalIndexPrice(assetCode);
            break;
          default:
            response = null;
        }

        if (response && response.success && response.data) {
          marketData = response.data;
          // 缓存数据
          this.setCachedData(cacheKey, marketData);
        }
      }

      if (marketData) {
        this.marketData[marketData.code || assetCode] = marketData;
        searchResultContainer.innerHTML = this.renderSearchResult(marketData);

        // 添加查看详情按钮事件
        const viewDetailsButton = searchResultContainer.querySelector<HTMLButtonElement>('.view-details-button');
        if (viewDetailsButton) {
          viewDetailsButton.addEventListener('click', () => {
            this.showMarketDetails(marketData, 'search');
          });
        }
      } else {
        searchResultContainer.innerHTML = `
          <div class="error-state">
            <div class="empty-state-icon">⚠️</div>
            <h4 class="empty-state-title">查询失败</h4>
            <p class="empty-state-description">无法获取 ${assetCode} 的市场数据</p>
          </div>
        `;
      }
    } catch (error) {
      console.error(`查询市场数据失败：${assetCode}`, error);
      searchResultContainer.innerHTML = `
        <div class="error-state">
          <div class="empty-state-icon">⚠️</div>
          <h4 class="empty-state-title">查询失败</h4>
          <p class="empty-state-description">获取市场数据时发生错误</p>
        </div>
      `;
    }
  }

  private renderSearchResult(data: any) {
    const changeClass = data.change_percent && data.change_percent >= 0 ? 'positive' : 'negative';
    const changeIcon = data.change_percent && data.change_percent >= 0 ? '📈' : '📉';
    const price = typeof data.price === 'number' ? data.price.toFixed(2) : '-';
    const changePercent = typeof data.change_percent === 'number' ? data.change_percent.toFixed(2) + '%' : 'N/A';
    const change = typeof data.change === 'number' ? (data.change >= 0 ? '+' : '') + data.change.toFixed(2) : '';

    return `
      <div class="search-result success scale-in">
        <h4>${data.name || '未知'}</h4>
        <p class="code">${data.code || ''}</p>
        <div class="price-info">
          <p class="price">${price}</p>
          <div class="change ${changeClass}">
            <span class="change-percent">${changeIcon} ${changePercent}</span>
            ${change ? `<span class="change-value">${change}</span>` : ''}
          </div>
        </div>
        ${data.volume ? `<p class="volume">📊 成交量：${data.volume}</p>` : ''}
        <p class="timestamp">🕐 ${new Date().toLocaleString()}</p>
        <button class="view-details-button btn btn-primary">📋 查看详情</button>
      </div>
    `;
  }

  private showMarketDetails(data: any, sourceTab: string = 'indices') {
    this.previousTab = sourceTab; // 记录来源标签
    this.currentTab = 'details';
    this.render();

    const marketDetailsContainer = this.container.querySelector('#market-details');
    if (marketDetailsContainer) {
      marketDetailsContainer.innerHTML = this.renderMarketDetails(data);
      // 绑定返回按钮事件
      this.bindBackButton();
      // 加载并绘制价格走势
      this.loadAndDrawPriceChart(data);
    }
  }

  private bindBackButton() {
    const backButton = this.container.querySelector<HTMLButtonElement>('#back-to-indices');
    if (backButton) {
      backButton.addEventListener('click', () => {
        this.currentTab = this.previousTab;
        this.render();
        if (this.currentTab === 'indices') {
          // 返回时使用缓存，不强制刷新
          this.loadSupportedIndices(false);
        }
      });
    }
  }

  // 加载并绘制价格走势
  private async loadAndDrawPriceChart(data: any) {
    const canvas = this.container.querySelector<HTMLCanvasElement>('#price-chart-canvas');
    if (!canvas) return;

    const code = data.code;
    const dataType = data.type || 'index';

    try {
      // 从后端获取历史价格
      const response = await api.getPriceHistory(code, dataType, 30);

      if (response.success && response.data && response.data.history.length > 0) {
        const history = response.data.history.map((item: any) => ({
          timestamp: new Date(item.timestamp).getTime(),
          price: item.price
        }));

        this.drawPriceChart(canvas, history, data.change_percent >= 0);
      } else {
        // 没有历史数据，显示提示
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = isDarkMode() ? '#94a3b8' : '#999';
          ctx.font = '14px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('暂无历史数据', canvas.width / 2, canvas.height / 2);
        }
      }
    } catch (error) {
      console.error('获取价格历史失败:', error);
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#e74c3c';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('获取历史数据失败', canvas.width / 2, canvas.height / 2);
      }
    }
  }

  // 绘制价格走势图
  private drawPriceChart(
    canvas: HTMLCanvasElement,
    history: { timestamp: number; price: number }[],
    isPositive: boolean
  ) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const padding = { top: 20, right: 20, bottom: 30, left: 60 };

    // 清空画布并填充背景
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = isDarkMode() ? '#1e293b' : '#ffffff';
    ctx.fillRect(0, 0, width, height);

    // 计算价格范围
    const prices = history.map(h => h.price);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const priceRange = maxPrice - minPrice || 1;

    // 绘制网格线
    ctx.strokeStyle = isDarkMode() ? '#475569' : '#e0e0e0';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + (height - padding.top - padding.bottom) * i / 4;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();

      // Y轴标签
      const price = maxPrice - priceRange * i / 4;
      ctx.fillStyle = isDarkMode() ? '#94a3b8' : '#666';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(price.toFixed(2), padding.left - 5, y + 4);
    }

    // 绘制价格线
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;

    ctx.strokeStyle = isPositive ? '#e74c3c' : '#27ae60'; // 红涨绿跌
    ctx.lineWidth = 2;
    ctx.beginPath();

    history.forEach((point, index) => {
      const x = padding.left + (chartWidth * index / (history.length - 1));
      const y = padding.top + chartHeight * (1 - (point.price - minPrice) / priceRange);

      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();

    // 绘制数据点
    history.forEach((point, index) => {
      const x = padding.left + (chartWidth * index / (history.length - 1));
      const y = padding.top + chartHeight * (1 - (point.price - minPrice) / priceRange);

      ctx.fillStyle = isPositive ? '#e74c3c' : '#27ae60';
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });

    // 绘制X轴时间标签（只显示首尾）
    if (history.length > 0) {
      ctx.fillStyle = isDarkMode() ? '#94a3b8' : '#666';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';

      const firstTime = new Date(history[0].timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
      const lastTime = new Date(history[history.length - 1].timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

      ctx.fillText(firstTime, padding.left, height - 10);
      ctx.fillText(lastTime, width - padding.right, height - 10);
    }

    // 绘制标题
    ctx.fillStyle = isDarkMode() ? '#f1f5f9' : '#333';
    ctx.font = 'bold 12px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('实时价格走势', width / 2, 15);
  }

  private renderMarketDetails(data: any) {
    const changeClass = data.change_percent && data.change_percent >= 0 ? 'positive' : 'negative';
    const changeIcon = data.change_percent && data.change_percent >= 0 ? '📈' : '📉';
    const price = typeof data.price === 'number' ? data.price.toFixed(2) : '-';
    const changePercent = typeof data.change_percent === 'number' ? data.change_percent.toFixed(2) + '%' : 'N/A';
    const change = typeof data.change === 'number' ? (data.change >= 0 ? '+' : '') + data.change.toFixed(2) : '';

    // 根据来源决定返回按钮文本
    const backButtonText = this.previousTab === 'search' ? '← 返回行情查询' : '← 返回指数列表';

    return `
      <div class="market-details-content scale-in">
        <div class="details-header">
          <button class="back-button" id="back-to-indices">${backButtonText}</button>
          <h4>${data.name || '未知'}</h4>
        </div>
        <p class="code" style="color: var(--text-tertiary); margin-bottom: 24px;">🏷️ ${data.code || ''}</p>

        <div class="details-grid">
          <div class="detail-item">
            <span class="label">📊 当前价格</span>
            <span class="value price">${price}</span>
          </div>

          ${data.change !== undefined ? `
            <div class="detail-item">
              <span class="label">📈 涨跌额</span>
              <span class="value ${changeClass}">${change}</span>
            </div>
          ` : ''}

          ${data.change_percent !== undefined ? `
            <div class="detail-item">
              <span class="label">📉 涨跌幅</span>
              <span class="value ${changeClass}">${changeIcon} ${changePercent}</span>
            </div>
          ` : ''}

          ${data.volume ? `
            <div class="detail-item">
              <span class="label">📊 成交量</span>
              <span class="value">${data.volume}</span>
            </div>
          ` : ''}

          <div class="detail-item">
            <span class="label">🕐 更新时间</span>
            <span class="value">${new Date().toLocaleString()}</span>
          </div>
        </div>

        <div class="chart-container">
          <h5>📈 价格走势</h5>
          <div id="price-chart" class="price-chart">
            <canvas id="price-chart-canvas" width="600" height="200" style="width: 100%; height: 200px;"></canvas>
          </div>
        </div>
      </div>
    `;
  }

  private renderIndicesLoader() {
    return `
      <div class="loader">
        <div class="loading-spinner"></div>
        <p class="loading-text">加载中...</p>
      </div>
    `;
  }
}

export const marketDataUI = new MarketDataUI();