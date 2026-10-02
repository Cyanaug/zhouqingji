# 诗集核验阅读 PDF（可选增强）

这条路线只服务于诗集工作台，不影响昼青集日常阅读，也不会修改 `corpus` 或 `results`。
网页“输出 PDF…”里的“快速保存”仍是零新增依赖的快捷方式；这里的工具用于需要跨电脑字体一致、
固定开本和自动验证时。

## 准备

1. 在诗集工作台完成选篇、编次和分辑，点击“翻页校样与输出”，再“进入输出排版”。
2. 点击“输出 PDF…”；普通使用直接选“快速保存”，核验环境未齐备时可选“导出增强用 HTML”。
   该 HTML 含私人诗文和随包字体，请自行妥善保存。
3. 如果界面显示“本机已具备固定开本排版与阅读 PDF 核验环境”，可直接点击“生成核验阅读 PDF”；程序只在
   系统临时目录处理，验证通过后由浏览器下载成品，不把 HTML 或 PDF 写进项目仓库。
4. 若想启用专业路径，电脑安装 Node.js 22.12 或更高版本，再安装 Vivliostyle CLI。两种装法
   任选其一：在昼青集目录里运行 `npm install @vivliostyle/cli`（约 240 MB，装在项目文件夹，
   不占系统盘，工具会自动识别）；或 `npm install -g @vivliostyle/cli`（装成全局命令）。
5. 若提示缺少验证器，运行 `python -m pip install pypdf fonttools`。两者只用于核验和修正 PDF
   文字映射，不参与网页排版。

项目不会静默下载以上增强依赖；不需要核验阅读 PDF 时完全不用安装。

当前随包字体已在制作阶段分开外形相同但编码不同的字形编号，快速保存 PDF 不再依赖
事后修复这些汉字映射，也不要求用户安装字体或 Python 增强组件。请从当前版本重新
导出 HTML/PDF；已有旧 PDF 不会被自动改写。

不确定电脑缺什么时，先运行只读体检（不会读取作品）：

```text
python theater/tools/book_pdf.py --doctor
```

## 环境要求对照（Windows）

| `--doctor` 字段 | 作用 | 不满足时 |
| --- | --- | --- |
| `node` ≥ v22.12 | 运行 Vivliostyle CLI | 从 nodejs.org 安装 LTS 或更高版本后重开终端 |
| `vivliostyle` | 排版渲染器 | 在昼青集目录 `npm install @vivliostyle/cli`（项目内，推荐）或 `npm install -g @vivliostyle/cli` |
| `pypdf` | 核验页数/尺寸/嵌字/文字层 | `python -m pip install pypdf` |
| `fonttools` | 修正 ToUnicode 部首别名 | `python -m pip install fonttools` |
| `browsers` | 渲染用的 Chrome/Edge | 装有 Edge 或 Chrome 即可；无需另下载浏览器 |

`ready` 只有五项全部就绪才为 `true`；缺任何一项时普通应用与“保存 PDF”路径照常可用。

## 常见报错对照

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| 未找到 Vivliostyle CLI | 未安装或不在 PATH | 按“准备”第 4 步安装；或用 `--vivliostyle` 指定路径 |
| 缺少 PDF 验证器 pypdf / fonttools | Python 环境不完整 | `python -m pip install pypdf fonttools` |
| 输出已存在 | 同名 PDF 已生成 | 确认后加 `--overwrite`，工具从不静默覆盖 |
| 排版 HTML 没有内嵌随包字体 / 清单不一致 | HTML 来自旧版本 | 从当前版本重新导出增强用 HTML |
| 仍有外部资源，不满足离线专业输出边界 | HTML 被手工改动 | 重新导出，不要手改导出文件 |
| Vivliostyle 构建失败（退出码 N） | 渲染器报错，信息附在错误尾部 | 按尾部提示处理；通常是 HTML 损坏或超时，可加 `--timeout 600` 重试 |
| PDF 页数与排印清单不一致 | 渲染不完整 | 重试；反复出现时用 `--check-html` 先核对清单 |

## 命令行备用方式

```text
python theater/tools/book_pdf.py "我的诗集-离线排版.html"
```

工具自动优先复用电脑已安装的 Edge/Chrome，避免另下载浏览器。也可以显式指定，例如：

```text
python theater/tools/book_pdf.py "我的诗集-离线排版.html" --browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

默认在 HTML 同目录生成：

- `我的诗集-专业阅读.pdf`：固定 A5/B5 尺寸、嵌入字体的阅读成品；
- `我的诗集-专业阅读.pdf.qa.json`：输入/输出哈希、页数、实际页面尺寸、字体嵌入和可提取文字检查。

渲染时会把自包含 HTML 放入隔离的同盘临时工作区；输入、输出各占一个目录，避免 Windows
跨盘路径被渲染器误判。原 HTML 不改写，渲染或核验失败时也不覆盖已有 PDF。

已有同名 PDF 时工具会拒绝覆盖；确认后才可加 `--overwrite`。只想检查 HTML 是否自包含，可运行：

```text
python theater/tools/book_pdf.py "我的诗集-离线排版.html" --check-html
```

## 交给打印店或印厂时

PDF 本身就是常见的交接格式，不要求作者改用某种专有排版工程。昼青集当前生成的是
**阅读样书 PDF**：首张包含标有“试排”的展示封面，适合阅读、家用打印和校样。它还不是可直接
送厂印刷的纯书芯文件；整张封面展开、书脊和勒口也尚未生成。

正式下单前，把核验阅读 PDF 发给厂商，并向对方逐项确认：成品开本、装订方式、纸张、是否需要
出血、封面展开模板、接受的 PDF 规范，以及展示封面是否需从书芯文件移出。若厂商要求 PDF/X、CMYK/专色、裁切标记
或特定出血值，应按其模板另做一个“印厂配置”，不能靠通用按钮猜测。

封面通常单独交付为一张展开 PDF。书脊宽度取决于最终页数、纸张厚度和装订方式，因此应在书芯
定稿、厂商给出模板后生成。腰封、护封、勒口和精装壳也是独立部件，不宜提前塞进书芯文件。

## 能保证与不能保证

工具把排印清单作为契约：PDF 页数必须一致，每页物理尺寸误差不超过 0.6 mm，使用到的字体必须
嵌入，并且排版页的每个文字片段都能从 PDF 原码提取。若 Chromium 把汉字错误映成外观相同的
部首兼容码位，工具会只修正 ToUnicode 文字映射，不改变页面字形；失败时不生成“通过”回执。

这仍叫“专业阅读 PDF”，不叫“印刷就绪 PDF”。印厂文件还需要根据实际装订确认封面展开、书脊、
出血、图片色彩空间、PDF/X 版本和印厂自己的预检规范；这些不能由一个通用按钮替作者和印厂决定。
