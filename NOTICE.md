# 内容与来源

本包提供 IELTS CLI 程序源码、运行所需的词库和原创练习内容，用于个人学习及朋友间分享。

五本词库取自 RealKai42/qwerty-learner-vscode 的固定提交 `e0dc7a09a5e0946d77b2c279f72a89daf3659b97`。

- 各版 `data/QWERTY-VSCODE-LICENSE.txt` 保留上游完整 MIT notice。
- 各版 `data/source.json`、`data/catalog.json`、`data/dicts/sources.json` 保留原始 URL、SHA-256 与来源说明。
- 分发副本时一并保留这些文件。词库释义未逐词校订，原始文件没有被改写。
- V2 的 `content.json` 包含 20 词原创例句及搭配练习，V3 扩展到 124 词；它们不是 IELTS 真题或词典引文。

本包不包含 Python、windows-curses、FFmpeg 二进制文件或预下载发音；这些组件由使用者从相应来源安装。按键播放时，仅将当前英语词条发送给有道发音服务，并把返回音频缓存在本地。

`PROVENANCE.json` 记录三版的来源验证摘要和通用版适配改动，`BUNDLE_MANIFEST.json` 与 `SHA256SUMS` 记录本包文件校验值。
