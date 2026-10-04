"""python -m subfix 入口。"""
try:
    from .subfix import main
except ImportError:  # 直接 python __main__.py 运行时的兜底
    from subfix import main

if __name__ == "__main__":
    main()
