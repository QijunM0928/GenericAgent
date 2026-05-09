# 项目宪法 — Desktop Pet v3

## 架构原则

### 1. 简洁性门控 (Simplicity Gate)
- 最简单的可行方案优先
- 引入新依赖需在 plan.md 中论证
- 视频素材预处理为 sprite sheet，运行时零额外依赖

### 2. 反抽象门控 (Anti-Abstraction Gate)
- 不为单一实现创建抽象层
- 仅 macOS 平台，删除 Windows 代码，无需跨平台抽象
- 出现第三个相似模式时才抽象

### 3. 集成优先门控 (Integration-First Gate)
- 优先使用已有基础设施（NSWindow + PyObjC）
- HTTP API 保持现有端口和协议
- 素材预处理脚本独立于运行时

### 4. 测试优先门控 (Test-First Gate)
- 行为状态机必须有测试覆盖
- 验收标准必须可自动化验证

## 技术约束
- 平台：macOS only，使用 PyObjC + NSWindow
- 素材格式：sprite sheet PNG（RGBA），预处理自 mp4 源视频
- Python 3.9+，Pillow + PyObjC
- HTTP 端口：51983

## 规格规范
- 单个 spec.md 不超过 2000 tokens
- 行为规格使用 EARS 句式
- 歧义必须标记 [NEEDS CLARIFICATION]
