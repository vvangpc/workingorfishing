"""开发入口与 PyInstaller 入口。PyInstaller 直接执行脚本时不会把 src 当包，
所以走一个顶层启动器，里面以包形式导入 src.main。"""
import sys

from src.main import main

if __name__ == "__main__":
    sys.exit(main())
