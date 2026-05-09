# Desktop Pet v3 实现计划

## 技术上下文
- 语言: Python 3.9+
- 框架: PyObjC + NSWindow (macOS only)
- 依赖: Pillow, PyObjC (运行时); ffmpeg (仅预处理)
- 存储: sprite sheet PNG + skin.json

## 实现步骤

1. [ ] 编写 `scripts/prepare_skin.py` — 视频素材预处理
   - 扫描源目录 mp4，按文件名映射状态名（图1→walk, 图2→idle, ...）
   - ffmpeg 逐帧提取为 RGBA PNG
   - 拼接为 sprite sheet（单行，columns=frameCount）
   - 生成 skin.json
   - 输出到目标 skin 目录

2. [ ] 运行预处理脚本，生成 leo_source skin

3. [ ] 重写 `desktop_pet_v3.pyw`
   - 删除所有 Windows/Tkinter 代码
   - 修复动画帧率：按 fps 配置动态调整 timer 间隔
   - 实现行为状态机：LOOP/EMOTION/DRAGGING 三类状态
   - 概率调度：40% idle, 30% 移动, 20% 情绪, 10% sprint
   - 状态相关移动速度和持续时间
   - 双击触发随机情绪动画
   - 拖拽后 2 秒延迟恢复行为
   - 保留：hitTest_ 透明穿透、右键菜单、Toast、HTTP API

4. [ ] 验证 12 条验收标准

## Constitution Gate 例外
无

## 测试策略
- SC-001: 运行 prepare_skin.py，检查输出文件
- SC-002~SC-012: 手动运行宠物，逐条验证行为
