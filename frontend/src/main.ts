import './style.css'
import { toast } from './toast'
import { fundManagerUI } from './fundManagerUI'
import { marketDataUI } from './marketDataUI'
import { goldTradingUI } from './goldTradingUI'
import { FundQuantUI } from './fundQuantUI'
import { QuantEngineUI } from './quantEngineUI'

let fundQuantUI: FundQuantUI | undefined

toast.init();

// 主题管理
class ThemeManager {
  private static readonly THEME_KEY = 'fund-valuation-theme';

  static init(): void {
    const savedTheme = localStorage.getItem(this.THEME_KEY);
    if (savedTheme === 'light') {
      document.body.classList.remove('dark-mode');
    } else {
      // Default to dark (terminal) mode
      document.body.classList.add('dark-mode');
    }
  }

  static toggle(): void {
    document.body.classList.toggle('dark-mode');
    const isDark = document.body.classList.contains('dark-mode');
    localStorage.setItem(this.THEME_KEY, isDark ? 'dark' : 'light');
  }

  static isDark(): boolean {
    return document.body.classList.contains('dark-mode') || window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
}

// 初始化主题
ThemeManager.init();

// 渲染主界面
document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <div class="app">
    <!-- 头部导航 -->
    <header class="app-header">
      <div class="app-title">
        <div class="app-title-icon">📈</div>
        <div>
          <h1>智能理财Agent</h1>
          <p class="app-title-subtitle">Wealth Terminal v1.0</p>
        </div>
      </div>
      <div class="app-nav">
        <button class="btn btn-ghost btn-icon" id="theme-toggle" title="切换主题">
          🌓
        </button>
      </div>
    </header>

    <!-- 标签页导航 -->
    <nav class="tabs" role="tablist">
      <button class="tab-button active" data-tab="fund-manager" role="tab" aria-selected="true">
        <span class="tab-button-icon">💼</span>
        基金管理
      </button>
      <button class="tab-button" data-tab="gold-trading" role="tab" aria-selected="false">
        <span class="tab-button-icon">📊</span>
        黄金量化
      </button>
      <button class="tab-button" data-tab="fund-quant" role="tab" aria-selected="false">
        <span class="tab-button-icon">📈</span>
        基金量化
      </button>
      <button class="tab-button" data-tab="market-data" role="tab" aria-selected="false">
        <span class="tab-button-icon">🌍</span>
        市场数据
      </button>
      <button class="tab-button" data-tab="quant-engine" role="tab" aria-selected="false">
        <span class="tab-button-icon">🧠</span>
        量化引擎
      </button>
    </nav>

    <!-- 标签内容区域 -->
    <main>
      <div class="tab-content active" id="fund-manager-container" role="tabpanel"></div>
      <div class="tab-content" id="gold-trading-container" role="tabpanel"></div>
      <div class="tab-content" id="fund-quant-container" role="tabpanel"></div>
      <div class="tab-content" id="market-data-container" role="tabpanel"></div>
      <div class="tab-content" id="quant-engine-container" role="tabpanel"></div>
    </main>
  </div>
`

// 主题切换按钮事件
const themeToggleBtn = document.getElementById('theme-toggle');
if (themeToggleBtn) {
  themeToggleBtn.addEventListener('click', () => {
    ThemeManager.toggle();
    const icon = themeToggleBtn as HTMLElement;
    icon.textContent = ThemeManager.isDark() ? '☀️' : '🌙';
  });
}

// 初始化界面
async function initApp() {
  // 初始化基金管理界面
  const fundManagerContainer = document.querySelector<HTMLDivElement>('#fund-manager-container')!
  fundManagerUI.init(fundManagerContainer).catch(console.error)

  // 初始化市场数据界面
  const marketDataContainer = document.querySelector<HTMLDivElement>('#market-data-container')!
  marketDataUI.init(marketDataContainer)

  // 初始化黄金量化交易界面
  const goldTradingContainer = document.querySelector<HTMLDivElement>('#gold-trading-container')!
  goldTradingUI.init(goldTradingContainer)

  // 初始化基金量化界面
  const fundQuantContainer = document.querySelector<HTMLDivElement>('#fund-quant-container')!
  fundQuantUI = new FundQuantUI()
  fundQuantUI.init(fundQuantContainer)

  // 初始化量化引擎界面
  const quantEngineContainer = document.querySelector<HTMLDivElement>('#quant-engine-container')!
  const quantEngineUI = new QuantEngineUI()
  quantEngineUI.init(quantEngineContainer)
}

initApp().catch(console.error)

// 标签切换功能（含标签保持：刷新后记住上次打开的标签）
function setupTabSwitching() {
  const tabButtons = document.querySelectorAll<HTMLButtonElement>('.tab-button')
  const tabContents = document.querySelectorAll<HTMLDivElement>('.tab-content')
  const ACTIVE_TAB_KEY = 'active_tab'

  // 初始化显示状态
  tabContents.forEach(content => {
    if (!content.classList.contains('active')) {
      content.style.display = 'none'
    }
  })

  function switchTab(tabId: string) {
    // 更新标签按钮状态
    tabButtons.forEach(btn => {
      btn.classList.remove('active')
      btn.setAttribute('aria-selected', 'false')
    })
    const targetBtn = document.querySelector<HTMLButtonElement>(`.tab-button[data-tab="${tabId}"]`)
    if (targetBtn) {
      targetBtn.classList.add('active')
      targetBtn.setAttribute('aria-selected', 'true')
    }

    // 更新标签内容状态
    const targetContent = document.getElementById(`${tabId}-container`)
    if (!targetContent) return

    tabContents.forEach(content => {
      content.classList.remove('active')
      content.style.display = 'none'
    })

    // 切离时通知当前活跃 Tab 暂停
    const currentActiveTab = document.querySelector<HTMLButtonElement>('.tab-button.active')
    if (currentActiveTab) {
      const prevTabId = currentActiveTab.dataset.tab
      if (prevTabId === 'fund-quant') fundQuantUI.onDeactivated?.()
    }

    targetContent.classList.add('active')
    targetContent.style.display = 'block'

    // 通知黄金量化Tab初始化图表
    if (tabId === 'gold-trading') {
      goldTradingUI.onActivated()
    }

    // 通知市场数据Tab加载数据（标签保持刷新时使用）
    if (tabId === 'market-data') {
      marketDataUI.onActivated()
    }

    // 通知基金量化Tab恢复刷新
    if (tabId === 'fund-quant') {
      fundQuantUI.onActivated?.()
    }

    // 添加动画效果
    targetContent.style.animation = 'none'
    targetContent.offsetHeight // 触发重绘
    targetContent.style.animation = 'fadeIn 0.3s ease-out'

    // 记住当前标签
    localStorage.setItem(ACTIVE_TAB_KEY, tabId)
  }

  tabButtons.forEach(button => {
    button.addEventListener('click', () => {
      const tabId = button.dataset.tab
      if (!tabId) return
      switchTab(tabId)
    })
  })

  // 恢复上次打开的标签
  const savedTab = localStorage.getItem(ACTIVE_TAB_KEY)
  if (savedTab && document.querySelector(`.tab-button[data-tab="${savedTab}"]`)) {
    switchTab(savedTab)
  }
}

setupTabSwitching()
