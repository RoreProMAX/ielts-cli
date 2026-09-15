# 贡献指南

感谢参与 IELTS CLI。请先阅读 README、[使用手册](docs/使用手册.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)，确认改动属于独立实现的范围。

## 开发环境

项目使用 Python 3.10+，运行时依赖标准库。Linux/macOS 的终端集成测试使用：

```sh
python3 -m pip install -r requirements-test.txt
cd apps/v3.2.0
python3 -m unittest discover -p 'test_*.py'
```

当前主要验证范围是 Linux：已完成 V3 的 120 项测试、重定位检查和离线容器验证。Windows 与 macOS 交互测试尚未在真机完成；跨平台 smoke CI 已配置，运行记录见 Actions。

## 提交改动

- 新功能应优先补充对应的 `unittest`，并说明验证命令和平台。
- 词库条目必须与现有章节和元数据结构相符；每章默认 20 词，末章可按实际数量结束。
- 例句或搭配内容的占位词必须与目标词相符，不能把例句中的其他词误当成练习目标。
- 原始字库文件不能直接修改。需要修正展示或筛选行为时，请改代码或添加外层元数据，并保留 `data/source.json` 中的 URL、固定 commit、SHA-256 和来源说明。
- 原创例句与搭配不是 IELTS 真题或词典引文；新增内容请确认版权来源并在变更说明中注明。
- 不要提交个人学习进度、`user-data/`、音频缓存、日志、账号、API key 或其他本机私有配置。
- 运行脚本不要依赖发布者的个人路径或凭证；保持移动目录后仍可运行。
- 保持 `--doctor`、发布包 `--verify` 和 `--app-version 1/2/3` 接口可用，除非变更说明明确记录兼容性影响。

提交前请检查 `git diff`、运行相关测试，并确认没有把发布包专用的 `BUNDLE_MANIFEST.json` 或个人数据混入源码提交。提交信息只描述变更内容。

例句材料的来源和已知排除项见 [CONTENT_SOURCES.md](apps/v3.2.0/CONTENT_SOURCES.md)。
