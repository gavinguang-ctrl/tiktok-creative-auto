# TikTok Creative Studio 自动化工具

自动在 TikTok Creative Studio 中批量生成广告视频。填写产品信息，一键提交，系统自动完成趋势选择、对话交互、视频生成和下载。

## 功能

- 自动填写提示词、上传产品图片
- 自动选择热门广告趋势（支持按行业分类）
- 批量生成：指定数量，依次选择不同趋势
- 自动点击对话回复，推进生成流程
- 生成完成后自动下载视频
- 历史任务保存，支持一键重新提交

## 环境要求

- Windows 10/11
- Google Chrome 浏览器
- 网络可访问 TikTok Creative Studio

**不需要**预装 Python 或其他开发工具（setup.bat 会自动安装）。

## 安装（首次使用）

1. 解压文件到任意目录
2. 双击 `setup.bat`，等待自动安装完成（约 5 分钟）
   - 自动下载嵌入式 Python
   - 安装依赖包
   - 安装 Playwright 浏览器引擎

## 使用方法

### 启动

双击 `start.bat`：
- 自动关闭并重启 Chrome（带调试端口）
- 启动本地服务
- 自动打开浏览器访问 `http://localhost:8000`

### 首次登录

启动后在弹出的 Chrome 中登录 TikTok Creative Studio：
- 访问 https://ads.tiktok.com/creative/creativestudio/chat
- 登录你的 TikTok 广告账号
- 登录状态会保存，之后无需重复登录

### 操作流程

1. 在网页表单中填写产品信息
2. 上传产品图片（注意图片要求）
3. 选择爆款趋势类目和生成数量
4. 点击「预览提示词」确认内容
5. 点击「确认并执行」开始自动生成
6. 等待系统自动完成（每个视频约 5-15 分钟）

### 图片要求

- ❌ **不要**上传带脸部的图片，截取脸部后再上传
- ✅ **要**上传有手持的图片，帮助 AI 理解产品大小

## 项目结构

```
tiktok_creative_auto/
├── main.py              # FastAPI 服务入口 + 任务调度
├── config.py            # 配置（选择器、超时、路径）
├── browser/
│   ├── __init__.py      # 公共 API 导出
│   ├── manager.py       # Chrome CDP 连接管理
│   ├── helpers.py       # 底层工具（重试、CDP点击、元素查找）
│   ├── images.py        # 图片上传（粘贴、卡住检测、清理）
│   ├── trends.py        # 趋势选择（行业、子分类、确认）
│   ├── chat.py          # 对话交互（发送、回复、生成检测）
│   ├── history.py       # 历史页下载（轮询、去重、状态筛选）
│   └── orchestrator.py  # 单轮提交主流程
├── models/
│   └── schemas.py       # 数据模型
├── services/
│   ├── template.py      # 提示词模板
│   └── translate.py     # 翻译服务
├── static/              # 前端页面
├── setup.bat            # 首次安装脚本
├── start.bat            # 启动脚本
├── downloads/           # 下载的视频保存目录
└── uploads/             # 上传的图片临时目录
```

## 常见问题

**Q: Chrome 没有被自动控制？**
A: 确保通过 `start.bat` 启动，不要手动打开 Chrome。如果已有 Chrome 在运行，start.bat 会先关闭它。

**Q: 提示"Cannot find element"？**
A: TikTok 页面可能更新了 UI。检查 `config.py` 中的 `SELECTORS` 配置是否需要更新。

**Q: 图片上传一直转圈？**
A: 系统会自动检测并重试。如果持续失败，检查图片格式（支持 PNG/JPG/WEBP）和大小。

**Q: 视频生成超时？**
A: 每个视频生成需要 5-15 分钟。系统会每 10 分钟轮询检查，最长等待 90 分钟。

## 技术栈

- Python + FastAPI（后端）
- Playwright（浏览器自动化，通过 CDP 连接 Chrome）
- 原生 HTML/CSS/JS（前端）
