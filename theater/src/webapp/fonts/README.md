# 昼青集随包排印字体

本目录只带一个正文所需字重，供诗集双页校样、阅读 PDF 与离线排版 HTML 使用。

- 字体：Source Han Serif CN Regular / 思源宋体 CN Regular
- 版本：2.003R
- 上游：<https://github.com/adobe-fonts/source-han-serif/releases/tag/2.003R>
- 上游包：`14_SourceHanSerifCN.zip`（Region Specific Subset OTFs Simplified Chinese）
- 上游包 SHA-256：`aa3f9134809de83bb2add0bd965e61421772ce2fd8628fe98516b77c93e1d819`
- 本文件 SHA-256：`3754ea669c530e2473354f8f6d9f79680a44d7e26ec7d00eeabee4a7e0753c5d`
- 许可证：SIL Open Font License 1.1，全文见 `OFL-1.1.txt`

字体未经修改，保留上游名称。当前 357 首语料的标题与正文共有 3211 个非空独特字符；
实际 cmap 检查只缺 7 个非汉字符号或 emoji（`๑ ᐛ ₃ ☺ ✌ U+FE0E 😭`），没有缺汉字。
缺失符号继续由 CSS 字体栈回退，不修改作品正文。

网页正常浏览时字体从本地 Release 文件加载，不访问在线字体服务。导出单文件离线排版 HTML
时，前端会把这份 OTF 转为 `data:` 字体嵌入，因此移动文件后仍保持同一正文用字。
