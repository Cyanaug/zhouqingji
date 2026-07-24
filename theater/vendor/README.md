# theater/vendor

第三方库以 **vendored（随仓库分发）** 方式内置于此，服务器"拷下来即可运行"，无需 `pip install`。

## jieba 0.42.1

中文分词库，用于词云（`theater/src/wordcloud_data.py`）。

- **许可**：MIT（见 `jieba/LICENSE`）。可自由使用、修改、再分发。
- **来源**：PyPI `jieba==0.42.1`（作者 Sun Junyi，https://github.com/fxsjy/jieba）。
- **裁剪**：只保留 `jieba.cut` 所需的核心——`__init__.py`、`_compat.py`、`dict.txt`、`finalseg/`。
  已移除用不到的 `analyse/`、`posseg/`、`lac_small/`（paddle 模型）等约 30MB，vendored 后约 7.4MB。
- **未改源码**：jieba 0.42.1 在 Python 3.12 下有两处弃用的正则转义（`\.` `\d`）会报 `SyntaxWarning`；
  为便于日后原样更新 jieba，不改其源码，改在 `wordcloud_data.py` 的 import 处静音该 warning。

若要升级 jieba：`pip download jieba==<ver> --no-deps`，解包后按上面的清单只拷核心文件覆盖即可。
