# 医用耗材批次追溯智能助手 — 便捷命令
# 用法：make <target>

PY ?= python

.PHONY: help install demo eval init config test serve clean

help:  ## 查看可用命令
	@echo "可用命令:"
	@echo "  make install   安装 Web/运行依赖"
	@echo "  make demo      离线演示业务 Agent 三条轨迹"
	@echo "  make eval      运行 30 条评测用例"
	@echo "  make init      初始化数据库与知识库索引"
	@echo "  make config    打印当前生效配置"
	@echo "  make test      运行离线回归测试(chat/admin)"
	@echo "  make serve     启动 Web 服务(需先 make install)"
	@echo "  make clean     清理 pycache 与本地数据库"

install:  ## 安装依赖
	pip install -r requirements.txt

demo:  ## 离线演示
	$(PY) cli.py demo

eval:  ## 评测
	$(PY) cli.py eval

init:  ## 初始化
	$(PY) cli.py init

config:  ## 配置
	$(PY) cli.py config

test:  ## 离线回归测试
	$(PY) test_chat_offline.py
	$(PY) test_admin_offline.py

serve:  ## 启动 Web 服务
	$(PY) cli.py serve

clean:  ## 清理
	$(PY) -c "import shutil,os; shutil.rmtree('__pycache__', ignore_errors=True); [os.remove(f) for f in ('agent.db',) if os.path.exists(f)]"
	@echo "cleaned."
