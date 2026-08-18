import logging
import os
import re
import time
from typing import Optional

import requests

from src.agent.factory import build_agent_executor


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# Environment
# ============================================================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# Agent Executor
# Build once and reuse for all Telegram requests.
executor = None


# ============================================================
# Telegram API
# ============================================================

def send_message(chat_id: int, text: str) -> bool:
    """
    Send a text message to Telegram.

    Telegram has a message size limit, so long AI reports are split
    into multiple messages.
    """

    if not text:
        text = "没有返回分析内容。"

    # Telegram text message limit is around 4096 characters.
    # Use a slightly smaller chunk to leave some margin.
    chunk_size = 3900

    chunks = [
        text[i:i + chunk_size]
        for i in range(0, len(text), chunk_size)
    ]

    success = True

    for chunk in chunks:
        try:
            response = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                },
                timeout=60,
            )

            if not response.ok:
                logger.error(
                    "Telegram send failed: HTTP %s | %s",
                    response.status_code,
                    response.text,
                )
                success = False

        except Exception:
            logger.exception("Telegram send exception")
            success = False

    return success


def get_updates(offset: Optional[int] = None):
    """
    Receive Telegram updates using long polling.
    """

    params = {
        "timeout": 50,
    }

    if offset is not None:
        params["offset"] = offset

    response = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params=params,
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# Stock parsing
# ============================================================

def extract_tickers(text: str):
    """
    Extract US stock tickers from a Telegram message.

    Supported examples:

        NVDA
        AMD
        TSLA

        /stock NVDA

        NVDA AMD TSM

    """

    text = text.upper().strip()

    # Remove /stock command.
    text = re.sub(r"^/STOCK\s*", "", text)

    candidates = re.findall(
        r"\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b",
        text,
    )

    ignored = {
        "START",
        "HELP",
        "MARKET",
        "STOCK",
        "THE",
        "AND",
        "FOR",
        "WITH",
    }

    return [
        ticker
        for ticker in candidates
        if ticker not in ignored
    ]


# ============================================================
# Agent prompt
# ============================================================

def build_stock_prompt(tickers):
    """
    Build the deep research prompt for the existing Agent.
    """

    ticker_text = ", ".join(tickers)

    return f"""
请对以下美国股票进行深度研究分析：

{ticker_text}

这是一个美股投资研究请求。

请优先使用你能够访问的实时股票数据、技术指标、新闻、基本面数据以及其他研究工具。

对于每一只股票，请尽可能分析：

1. 公司和股票简介
2. 当前价格和近期走势
3. 最近几个交易日的价格表现
4. 技术面
5. 趋势判断
6. 重要支撑位
7. 重要压力位
8. 成交量和市场资金特征（如果数据可用）
9. 最近重要新闻
10. 新闻对股价的潜在影响
11. 基本面情况
12. 最近财报及关键指标（如果数据可用）
13. 未来催化剂
14. 主要风险
15. 当前估值是否合理
16. 短线（未来几天到几周）观点
17. 中线（未来几个月）观点
18. 最终综合判断

请特别注意：

- 只分析美国股票。
- 不要编造实时价格、新闻、财务数据或技术指标。
- 如果某项数据无法获取，请明确说明。
- 对实时数据尽量使用最新可获得的数据。
- 区分事实、数据和你的分析判断。
- 不要把一般性的投资免责声明写得过长。
- 最终用中文回答。

请给出一个清晰、结构化、适合实际交易者阅读的研究报告。
""".strip()


# ============================================================
# Agent
# ============================================================

def get_executor():
    """
    Lazily create the Agent Executor.

    This avoids initializing the entire Agent system until the
    first stock query arrives.
    """

    global executor

    if executor is None:
        logger.info("正在创建 Agent Executor...")

        executor = build_agent_executor()

        logger.info("Agent Executor 创建完成。")

    return executor


def analyze_stock(chat_id: int, tickers):
    """
    Send the stock research request to the project's existing Agent.
    """

    agent = get_executor()

    prompt = build_stock_prompt(tickers)

    session_id = f"telegram_{chat_id}"

    logger.info(
        "开始股票分析 | tickers=%s | session=%s",
        ",".join(tickers),
        session_id,
    )

    result = agent.chat(
        message=prompt,
        session_id=session_id,
    )

    if not result.success:
        error = result.error or "Agent 分析失败"

        logger.error(
            "Agent analysis failed: %s",
            error,
        )

        return f"❌ 分析失败\n\n{error}"

    content = result.content or ""

    if not content.strip():
        return "❌ Agent 没有返回分析内容。"

    return content


# ============================================================
# Telegram commands
# ============================================================

def handle_start(chat_id: int):
    send_message(
        chat_id,
        (
            "🤖 Cake 美股 AI 分析 Bot\n\n"
            "直接发送美股代码即可进行深度分析。\n\n"
            "例如：\n"
            "NVDA\n\n"
            "多个股票：\n"
            "NVDA AMD TSM\n\n"
            "也可以使用：\n"
            "/stock NVDA\n\n"
            "/market - 美股市场复盘\n"
            "/help - 查看帮助"
        ),
    )


def handle_help(chat_id: int):
    send_message(
        chat_id,
        (
            "📖 使用方法\n\n"
            "① 查询股票\n\n"
            "直接输入：\n"
            "NVDA\n\n"
            "② 多股票分析\n\n"
            "NVDA AMD TSM\n\n"
            "③ 使用命令\n\n"
            "/stock NVDA\n"
            "/market\n"
            "/help\n\n"
            "Bot 会调用股票研究 Agent，综合行情、技术面、新闻、"
            "基本面和风险进行深度分析。"
        ),
    )


def handle_market(chat_id: int):
    """
    Market overview.

    This first version asks the Agent to analyze the major US market
    ETFs. We can later turn this into a dedicated market-review prompt.
    """

    send_message(
        chat_id,
        (
            "⏳ 正在进行美股市场深度复盘……\n\n"
            "正在获取市场数据和研究信息，请稍候。"
        ),
    )

    agent = get_executor()

    prompt = """
请进行今天的美国股市市场深度复盘。

只分析美国股票市场。

请综合使用最新可获得的市场数据、指数表现、新闻、宏观经济信息和市场研究工具。

重点分析：

1. Nasdaq
2. S&P 500
3. Dow Jones
4. VIX / 市场波动率（如果数据可用）
5. 美债收益率及利率预期
6. 美联储相关因素
7. 科技股和 AI 板块
8. 半导体板块
9. 当天最重要的市场新闻
10. 主要上涨/下跌板块
11. 市场风险
12. 明天值得关注的事件
13. 值得重点关注的美股

最后给出：

- 今日市场情绪
- 当前市场主线
- 明日重点关注方向
- 最大风险

请使用中文，并明确区分事实数据和分析判断。
""".strip()

    try:
        result = agent.chat(
            message=prompt,
            session_id=f"telegram_market_{chat_id}",
        )

        if not result.success:
            send_message(
                chat_id,
                f"❌ 市场复盘失败\n\n{result.error or '未知错误'}",
            )
            return

        send_message(
            chat_id,
            result.content or "❌ 没有返回市场复盘内容。",
        )

    except Exception as exc:
        logger.exception("Market analysis failed")

        send_message(
            chat_id,
            f"❌ 市场复盘失败：{exc}",
        )


# ============================================================
# Message handling
# ============================================================

def handle_message(message):
    """
    Handle one Telegram message.
    """

    chat = message.get("chat", {})

    chat_id = chat.get("id")

    if not chat_id:
        return

    # --------------------------------------------------------
    # Security: only allow your own Telegram account.
    # --------------------------------------------------------

    if ALLOWED_CHAT_ID:
        if str(chat_id) != ALLOWED_CHAT_ID:

            logger.warning(
                "拒绝未授权 Chat ID: %s",
                chat_id,
            )

            send_message(
                chat_id,
                "❌ 未授权的 Telegram 用户。",
            )

            return

    text = (message.get("text") or "").strip()

    if not text:
        return

    logger.info(
        "收到 Telegram 消息 | chat_id=%s | text=%s",
        chat_id,
        text,
    )

    # --------------------------------------------------------
    # /start
    # --------------------------------------------------------

    if text.lower().startswith("/start"):
        handle_start(chat_id)
        return

    # --------------------------------------------------------
    # /help
    # --------------------------------------------------------

    if text.lower().startswith("/help"):
        handle_help(chat_id)
        return

    # --------------------------------------------------------
    # /market
    # --------------------------------------------------------

    if text.lower().startswith("/market"):
        handle_market(chat_id)
        return

    # --------------------------------------------------------
    # Stock query
    # --------------------------------------------------------

    tickers = extract_tickers(text)

    if not tickers:

        send_message(
            chat_id,
            (
                "❌ 没有识别到美股代码。\n\n"
                "例如：\n"
                "NVDA\n\n"
                "或者：\n"
                "NVDA AMD TSM"
            ),
        )

        return

    # Limit to 5 stocks per request.
    tickers = tickers[:5]

    ticker_text = ", ".join(tickers)

    send_message(
        chat_id,
        (
            f"⏳ 正在深度分析：{ticker_text}\n\n"
            "正在调用股票研究 Agent 获取行情、新闻、"
            "技术面和基本面信息。\n\n"
            "分析可能需要几十秒到几分钟，请稍候。"
        ),
    )

    try:

        result = analyze_stock(
            chat_id,
            tickers,
        )

        send_message(
            chat_id,
            result,
        )

    except Exception as exc:

        logger.exception(
            "Stock analysis failed",
        )

        send_message(
            chat_id,
            f"❌ 分析过程中发生错误：{exc}",
        )


# ============================================================
# Telegram polling
# ============================================================

def main():

    logger.info("========================================")
    logger.info("🚀 Cake Stock Telegram Bot starting...")
    logger.info("========================================")

    logger.info(
        "Allowed Chat ID: %s",
        ALLOWED_CHAT_ID or "ALL",
    )

    offset = None

    while True:

        try:

            data = get_updates(
                offset=offset,
            )

            if not data.get("ok"):

                logger.error(
                    "Telegram API error: %s",
                    data,
                )

                time.sleep(5)

                continue

            updates = data.get(
                "result",
                [],
            )

            for update in updates:

                offset = (
                    update["update_id"] + 1
                )

                message = update.get(
                    "message"
                )

                if message:

                    try:

                        handle_message(
                            message
                        )

                    except Exception:

                        logger.exception(
                            "处理 Telegram 消息时发生错误"
                        )

        except requests.exceptions.RequestException:

            logger.exception(
                "Telegram network error"
            )

            time.sleep(5)

        except Exception:

            logger.exception(
                "Telegram polling error"
            )

            time.sleep(5)


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()
