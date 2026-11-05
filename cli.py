"""医用耗材批次追溯智能助手 — 统一命令行入口。

用法：
    python cli.py demo       # 离线演示业务 Agent 三条轨迹（检索→档案→库存→合规→申领HITL）
    python cli.py eval       # 运行 30 条评测用例并打印逐条结果与总分
    python cli.py init       # 初始化 SQLite 数据库与知识库内存索引
    python cli.py serve      # 启动 Web 服务（操作员问答 + 管理员后台，需安装 Web 依赖）
    python cli.py config     # 打印当前生效配置（离线/在线、密钥状态等）

说明：
    demo / eval / init / config 为零依赖离线模式，无需安装任何第三方包即可运行；
    serve 需要 pip install fastapi uvicorn jinja2 python-multipart 后使用。
"""

import argparse
import sys


def cmd_demo() -> None:
    """离线演示业务 Agent 三条轨迹。"""
    import demo_agent
    demo_agent.main()


def cmd_eval() -> None:
    """运行 30 条评测用例并打印得分。"""
    import eval_agent
    eval_agent.main()


def cmd_init() -> None:
    """初始化数据库与知识库索引。"""
    import agent_core
    agent_core.init_agent_db()
    agent_core.ensure_kb_ready()
    print("[ok] 数据库与知识库索引初始化完成。")


def cmd_config() -> None:
    """打印当前生效配置。"""
    import config
    c = config.load_config()
    print("===== 当前生效配置 =====")
    for k, v in c.items():
        if "SECRET" in k or "KEY" in k:
            print(f"  {k} = {'<已设置>' if v else '<未设置/离线>'}")
        else:
            print(f"  {k} = {v}")
    print(f"离线模式: {config.is_offline(c)}")


def cmd_serve() -> None:
    """启动 Web 服务。"""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("缺少依赖：请先执行  pip install fastapi uvicorn jinja2 python-multipart")
        sys.exit(1)
    import config
    import chat_app
    cfg = config.load_config()
    uvicorn.run(chat_app.app,
                host=cfg.get("HOST", "0.0.0.0"),
                port=config.as_int(cfg, "PORT", 8000))


_HANDLERS = {
    "demo": cmd_demo,
    "eval": cmd_eval,
    "init": cmd_init,
    "config": cmd_config,
    "serve": cmd_serve,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="医用耗材批次追溯智能助手 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("demo", help="离线演示业务 Agent 三条轨迹")
    sub.add_parser("eval", help="运行 30 条评测用例")
    sub.add_parser("init", help="初始化数据库与知识库")
    sub.add_parser("config", help="打印当前生效配置")
    sub.add_parser("serve", help="启动 Web 服务(问答+后台)")

    args = parser.parse_args()
    handler = _HANDLERS.get(args.cmd or "demo", cmd_demo)
    handler()


if __name__ == "__main__":
    main()
