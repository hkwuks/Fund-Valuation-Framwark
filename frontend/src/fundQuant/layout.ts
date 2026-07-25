/**
 * Panel 基类 + LayoutManager
 *
 * 每个面板继承 PanelBase，实现 mount() 和 refresh() 方法。
 * LayoutManager 将面板注册到 CSS Grid 并管理可见性。
 */

import { state } from './state'

export interface PanelConfig {
  id: string
  title: string
  defaultGridPos: { x: number; y: number; w: number; h: number }
}

export abstract class PanelBase {
  readonly id: string
  protected title: string
  el: HTMLElement | null = null
  gridPos: { x: number; y: number; w: number; h: number }

  constructor(config: PanelConfig) {
    this.id = config.id
    this.title = config.title
    this.gridPos = config.defaultGridPos
  }

  /** 创建 DOM 结构 */
  abstract render(): HTMLElement

  /** 挂载到容器 */
  mount(container: HTMLElement): void {
    this.el = this.render()
    container.appendChild(this.el)
    this.afterMount()
  }

  /** 挂载后的钩子（绑定事件、初始化数据） */
  protected afterMount(): void { /* override */ }

  /** 刷新面板数据 */
  abstract refresh(): Promise<void>

  /** 面板标题 */
  getTitle(): string { return this.title }
  setTitle(t: string): void { this.title = t }

  /** 销毁清理 */
  destroy(): void {
    this.el?.remove()
    this.el = null
  }
}

export class LayoutManager {
  private grid: HTMLElement
  private panels: Map<string, PanelBase> = new Map()
  // Coordinate conflict detection
  private occupied = new Set<string>()

  constructor(gridContainer: HTMLElement) {
    this.grid = gridContainer
  }

  private findFreeSlot(panel: PanelBase): { x: number; y: number } {
    let { x, y, w, h } = panel.gridPos
    while (this.occupied.has(`${x}-${y}`)) {
      y += 1  // 向下平移
    }
    for (let dy = 0; dy < h; dy++) {
      for (let dx = 0; dx < w; dx++) {
        this.occupied.add(`${x + dx}-${y + dy}`)
      }
    }
    return { x, y }
  }

  register(panel: PanelBase): void {
    this.panels.set(panel.id, panel)
    const layoutCfg = state.get('layout').panels[panel.id]
    if (layoutCfg?.visible === false) return

    const freePos = this.findFreeSlot(panel)
    panel.gridPos.x = freePos.x
    panel.gridPos.y = freePos.y
    panel.mount(this.grid)
    this.applyGridPos(panel)
  }

  private applyGridPos(panel: PanelBase): void {
    if (!panel.el) return
    const { x, y, w, h } = panel.gridPos
    panel.el.style.gridColumn = `${x + 1} / span ${w}`
    panel.el.style.gridRow = `${y + 1} / span ${h}`
  }

  async refreshAll(): Promise<void> {
    const promises: Promise<void>[] = []
    this.panels.forEach(p => {
      const cfg = state.get('layout').panels[p.id]
      if (cfg?.visible !== false) promises.push(p.refresh())
    })
    await Promise.allSettled(promises)
  }

  getPanel(id: string): PanelBase | undefined {
    return this.panels.get(id)
  }

  togglePanel(id: string): void {
    const cfg = state.get('layout').panels[id]
    if (!cfg) return
    const visible = !cfg.visible
    state.set('layout', {
      ...state.get('layout'),
      panels: { ...state.get('layout').panels, [id]: { ...cfg, visible } },
    })
    const panel = this.panels.get(id)
    if (!panel) return
    if (visible) {
      // 重新挂载
    } else {
      panel.destroy()
      this.panels.delete(id)
    }
  }

  destroy(): void {
    this.panels.forEach(p => p.destroy())
    this.panels.clear()
  }
}
