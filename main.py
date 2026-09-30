"""入口。"""

import asyncio

from novalark import App


def main() -> None:
    asyncio.run(App().run())


if __name__ == "__main__":
    main()
