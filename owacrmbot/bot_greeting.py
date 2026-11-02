# Ushbu fayl asosiy main.py boshqaruv paneli tizimini ishga tushiradi
import asyncio
from main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Dastur to'xtatildi.")
