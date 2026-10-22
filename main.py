"""CLI 入口（复用 src/pipeline.py）。

用法：
  python main.py "你的项目需求"

流水线会自动判断：纯后端 / 纯前端 / 前后端并行。
"""
import argparse
import asyncio

from src.pipeline import run_pipeline


def on_event(kind, source, text):
    if kind == "stage":
        print(f"\n===== {text} =====\n")
    elif kind == "text":
        print(f"---------- {source} ----------\n{text}")
    elif kind == "tool":
        print(f"[工具] {text}")
    elif kind == "done":
        print(f"\n✅ 完成。产出目录：{text}")
    elif kind == "error":
        print(f"\n❌ 错误：{text}")


async def ask_cli(prompt, kind):
    return (await asyncio.to_thread(input, prompt + " ")).strip()


async def main():
    ap = argparse.ArgumentParser(description="本地群聊式 AI 软件团队")
    ap.add_argument("requirement", nargs="+", help="原始项目需求")
    args = ap.parse_args()
    await run_pipeline(" ".join(args.requirement), on_event, ask_cli)


if __name__ == "__main__":
    asyncio.run(main())
