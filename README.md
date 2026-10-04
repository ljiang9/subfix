# subfix

SRT 字幕工具箱：移位 / 变速 / 合并 / 切分 / 清洗 / LLM 翻译 / 检查。

给短视频创作者和字幕组用的小工具 —— Python 标准库零依赖，单个文件拎走即用。

## 安装

零依赖，Python 3.10+ 即可：

```bash
python3 -m subfix --help
# 或
python3 subfix.py check examples/sample.srt
```

## 快速开始

```bash
# 检查字幕问题
subfix check video.srt

# 整体延后 2.5 秒（负数向前，0 处钳制）
subfix shift video.srt +2.5 -o fixed.srt

# 25fps -> 24fps 重定时
subfix scale video.srt 1.0417 -o fixed.srt

# 拼接两段字幕，中间留 1 秒
subfix merge part1.srt part2.srt --gap 1.0 -o full.srt

# 按 30 秒一段切分
subfix split long.srt 30 -o out/

# 清洗：去 HTML/ASS 标签、♪、重复行，删空，重编号
subfix clean raw.srt -o clean.srt

# 翻译成英文（时间轴不变）
export OPENAI_API_KEY=sk-...
subfix translate video.srt --to en -o video.en.srt

# 先看会发什么，不真正调用
subfix translate video.srt --to en --dry-run
```

## 子命令一览

| 子命令 | 说明 |
|---|---|
| `shift in.srt +2.5` | 平移全部时间戳（秒，负数向前），起点钳制在 0 |
| `scale in.srt 1.1` | 按倍率缩放时间轴，`--anchor start\|center` 选锚点 |
| `merge a.srt b.srt --gap 1.0` | 拼接，b 整体后移到 a 结尾 + 间隔 |
| `split in.srt 30` | 按 N 秒切块（不断开单条字幕），输出 `-part01.srt`… |
| `clean in.srt` | 去 `<i>`/`<b>` 等 HTML 标签、`{...}` ASS 覆盖码、♪♫、连续重复行；删空字幕；重编号 |
| `translate in.srt --to en` | LLM 翻译文本行，时间轴原样保留；key 取 `OPENAI_API_KEY`（或 `SUBFIX_API_KEY`），绝不打印 |
| `check in.srt` | 校验：编号连续性、时间重叠、空字幕、语速（字/秒）警告 |

所有写文件的子命令都支持 `-o/--output`（缺省打印到 stdout）和 `--bom`（输出带 BOM，兼容老播放器）。

## 解析健壮性

- 自动处理 BOM、`\\r\\n`、文件末尾缺空行
- 读取时自动尝试 `utf-8-sig → utf-8 → gbk → latin-1`
- `check` 会报：编号断裂、字幕重叠、空字幕、开始≥结束、语速超阈值（默认 20 字/秒）、单条短于 0.8 秒

## 翻译说明

- 按 `--batch`（默认 20 条）分批调用 OpenAI 兼容 `/chat/completions` 接口
- 系统提示要求模型只返回 JSON 数组，程序做数量校验，对不上就报错退出（不写坏文件）
- `--dry-run` 只打印请求预览，不联网、不需要 key

## License

MIT
