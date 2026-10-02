# 昼青集随包排印字体

本目录只带一个正文所需字重，供诗集双页校样、阅读 PDF 与离线排版 HTML 使用。

- 字体：ZQ Book Song Regular（基于思源宋体 CN Regular）
- 版本：2.003.1；上游版本 2.003R
- 上游：<https://github.com/adobe-fonts/source-han-serif/releases/tag/2.003R>
- 上游包：`14_SourceHanSerifCN.zip`（Region Specific Subset OTFs Simplified Chinese）
- 上游包 SHA-256：`aa3f9134809de83bb2add0bd965e61421772ce2fd8628fe98516b77c93e1d819`
- 上游 OTF SHA-256：`3754ea669c530e2473354f8f6d9f79680a44d7e26ec7d00eeabee4a7e0753c5d`
- 随包 `ZQBookSong-Regular.otf` SHA-256：`e6a906c9f2f472d7b55b23dd80774818d00ae54c5766b682f502c4010822d29d`
- 许可证：SIL Open Font License 1.1，全文见 `OFL-1.1.txt`

派生版将 504 个共享字形编号的码位分开，避免浏览器生成 PDF 时把汉字误标成外形相同的
部首或兼容字符。没有删掉字符：Unicode 覆盖、字形轮廓和字宽保持一致；文件仅增加约 114 KiB。
按 OFL 要求改用新名称，保留上游版权和许可证。未覆盖的符号继续由系统字体回退，不修改作品正文。

派生文件由 `theater/tools/build_book_font.py` 从上述哈希的原始 OTF 确定性生成。
生成工具需要 fonttools，但只供维护者使用；用户打开网页、导出 HTML 或快速保存 PDF 不新增依赖。

网页正常浏览时字体从本地 Release 文件加载，不访问在线字体服务。导出单文件离线排版 HTML
时，前端会把这份 OTF 转为 `data:` 字体嵌入，因此移动文件后仍保持同一正文用字。
