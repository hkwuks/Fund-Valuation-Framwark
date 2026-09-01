# 贡献与 PR 规范

## 分支与合并

- `main` 始终保持可测试、可发布状态，是所有 PR 的默认目标分支。
- 从最新 `main` 创建短生命周期分支，建议 1–3 天内完成合并。
- 分支命名：`feature/<描述>`、`fix/<描述>`、`refactor/<描述>`、`chore/<描述>`。
- 禁止直接推送 `main`；通过 PR 合并，合并后删除已合并分支。

## 提交信息

使用 Conventional Commits 格式：`<type>: <简短说明>`。常用类型：

- `feat`：新增功能
- `fix`：缺陷修复
- `refactor`：不改变行为的重构
- `test`：测试变更
- `docs`：文档变更
- `chore`：依赖、CI 或其他工具变更

每个提交只做一件逻辑上的事，提交说明描述目的而不只是罗列文件名。

## PR 要求

1. PR 标题遵循 Conventional Commits，并明确用户可感知的影响。
2. PR 描述填写变更背景、方案、验证结果、风险和回滚方式。
3. 保持 PR 小而聚焦；大型改动应拆分为可独立审查的 PR。
4. 新行为必须有测试；行为变化同步更新文档或示例。
5. 不提交 `.env`、令牌、密钥、个人数据、缓存和构建产物。
6. 所有 CI 检查通过且至少一名维护者批准后才能合并。

## 本地验证

后端：

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

前端：

```bash
cd frontend
npm ci
npm run build
```

CI 会在针对 `main` 的 PR 以及推送到 `main` 时自动运行后端测试、前端构建和依赖漏洞审计。本项目当前不包含自动部署流程；发布和部署由维护者另行安排。
