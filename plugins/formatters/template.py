"""模板格式化：用占位符重排正文，加页眉页脚、标签、按钮。

可用占位符：
    {text} {title} {url} {author} {source} {date} {time} {datetime} {tags}
"""
from __future__ import annotations

import datetime as dt

from teleops.core import FormatterPlugin, Item, field


class TemplateFormatter(FormatterPlugin):
    name = "template"
    display_name = "模板排版"
    version = "1.0.0"
    author = "TeleOps"
    description = "用模板重排正文，支持页眉/页脚/标签/来源链接与阅读原文按钮。"

    config_schema = [
        field("template", "正文模板", "text",
              default="{text}",
              help="可用占位符：{text} {title} {url} {author} {source} {date} {time} {datetime} {tags}",
              group="正文"),
        field("header", "页眉", "text", default="", group="正文"),
        field("footer", "页脚", "text", default="", group="正文",
              placeholder="例如：\n\n📢 <a href=\"https://t.me/yourchannel\">订阅本频道</a>"),
        field("tags", "追加标签", "string", default="",
              help="空格或逗号分隔，会自动加 #。", group="正文"),
        field("parse_mode", "解析模式", "select", default="keep",
              options=[
                  {"value": "keep", "label": "沿用来源"},
                  {"value": "html", "label": "HTML"},
                  {"value": "md", "label": "Markdown"},
                  {"value": "none", "label": "纯文本"},
              ], group="正文"),
        field("button_text", "底部按钮文字", "string", default="", group="按钮"),
        field("button_url", "底部按钮链接", "string", default="",
              help="留空则用原文链接 {url}。", group="按钮"),
        field("skip_if_empty", "正文为空则丢弃", "bool", default=False, group="其他"),
    ]

    async def format(self, item: Item, ctx) -> Item:
        now = dt.datetime.now()
        published = item.published_at or now
        tags_cfg = (self.get("tags", "") or "").replace(",", " ").split()
        all_tags = [t if t.startswith("#") else f"#{t}" for t in (item.tags + tags_cfg) if t]

        values = {
            "text": item.text or "",
            "title": item.title or "",
            "url": item.url or "",
            "author": item.author or "",
            "source": item.source_name or "",
            "date": published.strftime("%Y-%m-%d"),
            "time": published.strftime("%H:%M"),
            "datetime": published.strftime("%Y-%m-%d %H:%M"),
            "tags": " ".join(all_tags),
        }

        body = _safe_format(self.get("template", "{text}") or "{text}", values)
        header = _safe_format(self.get("header", "") or "", values)
        footer = _safe_format(self.get("footer", "") or "", values)
        item.text = "".join([header, body, footer]).strip()

        if self.get("skip_if_empty", False) and not item.text and not item.has_media:
            return item.drop("模板渲染后为空")

        mode = self.get("parse_mode", "keep")
        if mode != "keep":
            item.parse_mode = None if mode == "none" else mode

        item.tags = all_tags

        btn_text = self.get("button_text", "")
        btn_url = self.get("button_url", "") or item.url
        if btn_text and btn_url:
            item.buttons = item.buttons + [[{"text": btn_text, "url": _safe_format(btn_url, values)}]]

        return item


def _safe_format(tpl: str, values: dict[str, str]) -> str:
    """占位符替换；未知占位符原样保留，不抛异常。"""
    out = tpl
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out
