# 第三方声明

本项目是独立的 Python 实现，受 Qwerty 打字学词思路启发，但不是 `qwerty-learner` 或 `qwerty-learner-vscode` 的官方 fork，也不表示任何上游认可或维护关系。

## 字库

随项目分发的五本字库取自 `RealKai42/qwerty-learner-vscode` 的固定提交：

`e0dc7a09a5e0946d77b2c279f72a89daf3659b97`

各版本的 `apps/v*/data/source.json` 保留每本字库的原始 URL、SHA-256、抓取时间和来源元数据；各版本的 `apps/v*/data/QWERTY-VSCODE-LICENSE.txt` 保留随字库来源提供的完整 MIT notice。分发时请保留这些文件，不要把原始字库改写后冒充原始文件。

字库来源仓库：<https://github.com/RealKai42/qwerty-learner-vscode>

## 相关上游项目

主项目 `RealKai42/qwerty-learner` 的当前仓库许可证是 GPL-3.0：<https://github.com/RealKai42/qwerty-learner/blob/master/LICENSE>

本项目的 Python 应用代码为独立实现，采用根目录 LICENSE 中的 GPL-3.0-only。上游主项目及复用词库的许可证分别记录，随词库附带的 MIT notice 保持不变。

主项目的数据来源说明：<https://github.com/RealKai42/qwerty-learner#数据来源>

## 音频与运行依赖

项目不捆绑 Python、`windows-curses`、FFmpeg/`ffplay` 二进制文件或预下载发音。Windows 依赖版本固定为 `windows-curses==2.4.2`，详见 `requirements-windows.txt`；测试依赖见 `requirements-test.txt`。

按键请求发音时，程序向有道发音服务发送当前英语词条，并将返回音频缓存到本地。使用者应自行确认网络服务的条款、可用性和适用地区；文字练习不依赖该服务。

## 原创内容

`apps/v2.0.0/content.json` 和 `apps/v3.2.0/content.json` 中的例句与搭配由本项目维护，V3 当前覆盖 124 个词。它们不是 IELTS 真题，也不是从词典逐段复制的引文。

独立应用代码以 GPL-3.0-only 分发；第三方词库的原有 MIT notice 保留，不替换其版权声明。
