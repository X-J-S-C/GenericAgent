"""CLI 接口"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="OAK-GenericAgent: 融合 OpenAkita 与 GenericAgent 的 Agent 框架"
    )
    
    parser.add_argument(
        "--query", "-q",
        help="要执行的查询"
    )
    
    parser.add_argument(
        "--model", "-m",
        default="claude-opus-4",
        help="使用的模型"
    )
    
    parser.add_argument(
        "--planner",
        choices=["react", "tot", "reflexion"],
        default="react",
        help="使用的规划策略"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出"
    )
    
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        help="最大执行轮数"
    )
    
    args = parser.parse_args()
    
    if args.query:
        asyncio.run(run_query(args))
    else:
        interactive_mode(args)


async def run_query(args):
    """执行单次查询"""
    print(f"[OAK-GenericAgent] 执行查询: {args.query}")
    print(f"  模型: {args.model}")
    print(f"  规划器: {args.planner}")
    print(f"  最大轮数: {args.max_turns}")
    print()
    
    try:
        from oak_gkernel.core.agent_kernel import AgentConfig, BaseAgentKernel
        
        config = AgentConfig(
            name="oak-ga-agent",
            max_turns=args.max_turns,
            verbose=args.verbose,
            planner=args.planner
        )
        
        print("[Info] 初始化 Agent 内核...")
        print("[Info] 提示：完整实现需要配置 LLM 和工具适配器")
        print()
        
        print(f"[Result] Agent 架构已就绪")
        print(f"  - 内核: BaseAgentKernel")
        print(f"  - 规划器: {args.planner}")
        print(f"  - 记忆: LayeredMemory (L0-L4)")
        print(f"  - 工具: GenericAgentToolRegistry")
        
    except ImportError as e:
        print(f"[Error] 导入模块失败: {e}")
        print("[Hint] 请确保已安装 oak-genericagent 或在开发模式下运行")
        sys.exit(1)


def interactive_mode(args):
    """交互模式"""
    print("=" * 60)
    print("OAK-GenericAgent 交互模式")
    print("=" * 60)
    print()
    print("输入你的查询，按 Enter 执行")
    print("输入 'quit' 或 'exit' 退出")
    print()
    
    while True:
        try:
            query = input("> ").strip()
            
            if query.lower() in ("quit", "exit", "q"):
                print("再见！")
                break
            
            if not query:
                continue
            
            asyncio.run(run_query(argparse.Namespace(
                query=query,
                model=args.model,
                planner=args.planner,
                verbose=args.verbose,
                max_turns=args.max_turns
            )))
            
        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"[Error] {e}")


if __name__ == "__main__":
    main()
