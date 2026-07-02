# 小红书采集 GUI

一个本地运行的小红书内容解析与下载工具。粘贴小红书链接或整段分享口令后，可以先解析出笔记标题、正文、作者、发布时间、图片或视频列表，再选择下载全部或部分内容。

本项目是 [`JoeanAmier/XHS-Downloader`](https://github.com/JoeanAmier/XHS-Downloader) 的轻量 GUI 包装，不接入第三方解析网站。页面运行在本机 `127.0.0.1:8765`，下载结果保存到当前用户的下载文件夹。

## 功能

- 支持小红书网页链接、分享链接和整段口令识别。
- 解析笔记标题、正文、作者、发布时间、作品类型和媒体列表。
- 图文笔记支持全选、反选、下载所选、下载全部。
- 视频笔记支持整条下载。
- 图片缩略图通过本地代理预览，减少浏览器直接加载小红书 CDN 失败的问题。
- 正文超过 3 行时默认折叠，可点击“查看全文”展开。
- 下载输出统一保存到当前用户下载文件夹下的 `XHS-Downloads`。
- 不生成本地作品数据库 `ExploreData.db`。

## 环境要求

- macOS、Windows 或 Linux
- Python 3.12+
- `uv`
- Git submodule 支持

## 安装

首次 clone 后执行：

macOS / Linux:

```bash
git submodule update --init --recursive
./setup.sh
```

Windows PowerShell:

```powershell
git submodule update --init --recursive
.\setup.ps1
```

## 启动 GUI

macOS / Linux:

在项目根目录执行：

```bash
./start-xhs-gui.sh
```

Windows PowerShell:

```powershell
.\start-xhs-gui.ps1
```

Windows 双击:

```text
start-xhs-gui.bat
```

启动后打开：

```text
http://127.0.0.1:8765
```

## 使用方法

1. 从小红书复制链接或整段分享口令。
2. 粘贴到输入框。
3. 点击“开始解析”。
4. 查看标题、正文和媒体列表。
5. 按需要选择图片，然后点击“下载所选”或“下载全部”。
6. 下载结果在：

- macOS / Linux: `~/Downloads/XHS-Downloads/`
- Windows: `%USERPROFILE%\Downloads\XHS-Downloads\`

## 命令行下载

也可以直接使用包装脚本：

macOS / Linux:

```bash
./download-xhs.sh 'https://xhslink.com/xxxx'
```

Windows PowerShell:

```powershell
.\download-xhs.ps1 'https://xhslink.com/xxxx'
```

多个链接可以放在同一组引号里，用空格分隔。

## 注意

- Cookie 不是必需项；当前 GUI 默认不展示 Cookie 输入。
- 平台接口和风控可能变化，解析速度和成功率取决于小红书页面访问状态。
- 请只下载自己有权保存或分析的内容。
