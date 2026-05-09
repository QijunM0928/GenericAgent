# Desktop Pet v3 规格文档

## 1. 目标

重构桌面宠物，解决 v2 的核心问题：行为系统过于随机、交互体验粗糙、视频素材无法使用。仅保留 macOS 实现，删除 Windows 代码。视频素材通过预处理脚本转为 sprite sheet PNG，运行时零额外依赖。

## 2. 接口契约

### HTTP API（保持兼容）
```
GET  /?state=<name>     → 200 "ok" | 400
GET  /?msg=<text>       → 200 "ok"
GET  /?status           → 200 {"state":str, "skin":str, "direction":int}
POST /  body=<text>     → 200 "ok"
```

### CLI
```bash
python3 desktop_pet_v3.pyw                          # 启动
python3 scripts/prepare_skin.py <source_dir> <out>  # 预处理素材
```

### 右键菜单
- Skins → 列出可用皮肤
- Animation → 列出当前皮肤动画状态
- Quit → 退出

## 3. 数据模型

### BehaviorState（行为状态机）
```
IDLE ←→ WALK ←→ RUN
  ↕         ↕
FLY       SPRINT
  ↓
[EMOTION] → angry | happy | sad | surprise | bow | jump
  ↓ (自动)
IDLE
```

- LOOP 状态：idle, walk, run, sprint, fly — 可持续停留
- EMOTION 状态：angry, happy, sad, surprise, bow, jump — 播完自动回 idle
- DRAGGING：拖拽时挂起行为，松开后恢复

### SkinConfig
```json
{
  "name": str,
  "size": {"width": int, "height": int},
  "animations": {
    "<state>": {
      "file": str,
      "loop": bool,
      "sprite": {"frameWidth":int, "frameHeight":int, "frameCount":int, "columns":int, "fps":int}
    }
  }
}
```

## 4. 行为规格

### 4.1 动画帧率 [P1]
The system shall 按每个动画配置的 fps 值播放帧序列，帧间隔 = 1000/fps ms。

### 4.2 状态机转换 [P1]
When 一个非循环动画播放完毕, the system shall 自动转换到 idle 状态。
When 用户通过右键菜单或 HTTP API 请求状态切换, the system shall 立即切换到目标状态并重置帧索引。
While 处于 DRAGGING 状态, the system shall 暂停行为定时器和移动定时器。
When 拖拽结束, the system shall 恢复行为定时器，延迟 2 秒后恢复自动行为。

### 4.3 自动行为调度 [P1]
When 行为定时器触发, the system shall 按以下概率选择下一行为：
- 40% → idle（停留原地）
- 30% → 随机循环移动状态（walk/run/fly）+ 随机方向 + 开始移动
- 20% → 随机情绪动画（原地播放）
- 10% → sprint（高速移动）

行为定时器间隔：idle 后 5-10 秒，移动后 3-6 秒，情绪后 2-4 秒。

### 4.4 移动行为 [P1]
While 处于移动状态（walk/run/sprint/fly）, the system shall 按状态对应速度水平移动：
- walk: 2px/帧
- run: 4px/帧
- sprint: 6px/帧
- fly: 3px/帧

When 窗口到达屏幕左右边界, the system shall 反转方向并更新动画朝向。
When 移动定时器到期, the system shall 停止移动并转换到 idle。

移动持续时间：walk 3-6 秒，run 2-4 秒，sprint 1-2 秒，fly 3-7 秒。

### 4.5 拖拽交互 [P1]
When 用户在宠物不透明区域按下鼠标左键, the system shall 进入拖拽模式。
While 拖拽中, the system shall 跟随鼠标移动窗口位置。
When 释放鼠标, the system shall 退出拖拽模式。

### 4.6 双击保护 [P2]
When 用户双击宠物, the system shall 触发一个随机情绪动画（从 angry/happy/sad/surprise/bow/jump 中随机选择）。

### 4.7 Toast 消息 [P2]
When 收到 HTTP msg 参数或 POST body, the system shall 在宠物上方显示气泡消息，3 秒后自动消失。
If 新消息到达时旧消息仍在显示, the system shall 替换旧消息并重置计时器。

### 4.8 皮肤切换 [P2]
When 用户从右键菜单选择新皮肤, the system shall 加载新皮肤配置，调整窗口尺寸，重置为 idle 状态。

### 4.9 素材预处理 [P1]
When 运行 prepare_skin.py --chroma-key, the system shall：
1. 扫描源目录中的 mp4 文件
2. 用 ffmpeg 逐帧提取为 RGBA PNG
3. 对每帧执行 flood-fill 背景去除：从图像边缘 BFS 扩散，仅去除与边缘连通的青绿色背景像素，保留角色内部同色像素
4. 拼接为 sprite sheet PNG
5. 生成 skin.json 配置
6. 输出到目标 skin 目录

### 4.10 单实例保护 [P2]
When 启动时检测到端口 51983 已被占用, the system shall 提示用户并退出。

## 5. 约束条件
- 仅 macOS 平台，使用 PyObjC + NSWindow
- 运行时依赖：Pillow, PyObjC（无 ffmpeg/opencv 依赖）
- 素材预处理依赖：ffmpeg（仅构建时）
- 显示尺寸：默认 120x140，支持 skin.json 配置
- 内存：单个 sprite sheet 不超过 50MB

## 6. 验收标准

- [ ] SC-001: 运行 prepare_skin.py --chroma-key 能将 leo_source 的 9 个 mp4 转为 9 个 sprite sheet PNG + skin.json，背景已去除，角色内部青绿色像素保留
- [ ] SC-002: 启动后宠物显示在屏幕底部中央，播放 idle 动画
- [ ] SC-003: 动画按 skin.json 中配置的 fps 播放，帧间隔误差 < 10ms
- [ ] SC-004: 非循环动画播完后自动回到 idle
- [ ] SC-005: 自动行为按 4.3 的概率分布调度，连续观察 20 次行为切换符合分布
- [ ] SC-006: walk/run/sprint/fly 各自以对应速度移动
- [ ] SC-007: 窗口到达屏幕边界时反转方向
- [ ] SC-008: 拖拽时行为暂停，松开后 2 秒恢复
- [ ] SC-009: 双击不退出程序
- [ ] SC-010: HTTP API ?state=walk 能切换状态，?msg=hello 能显示 Toast
- [ ] SC-011: 代码中无 Windows/Tkinter 相关代码
- [ ] SC-012: 右键菜单可切换皮肤和动画

## 7. 不包含
- Windows 平台支持
- 视频运行时解码（仅预处理）
- 声音/音效
- 多宠物实例
- 网络远程控制（仅本地 HTTP）
- 宠物间互动
- 屏幕边缘攀爬/重力物理

## 8. 替代方案

### 方案 A: 预处理 sprite sheet（当前方案）
- 优势: 运行时零额外依赖，内存可控，性能好
- 劣势: 素材更新需重新预处理，sprite sheet 文件较大

### 方案 B: 运行时 ffmpeg 解码视频
- 优势: 素材更新无需预处理，支持更多格式
- 劣势: 依赖 ffmpeg 运行时，内存开销大（逐帧解码），启动慢

## 变更记录
| 版本 | 日期 | 变更摘要 |
|------|------|----------|
| v1 | 2026-05-01 | 初始规格 |
