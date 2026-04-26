#!/usr/bin/env python3
# 测试 memory-lancedb-pro 和 openclaw-wiki-lancedb 功能

import os
import sys
import time

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_memory_lancedb_pro():
    print("\n=== 测试 memory-lancedb-pro ===")
    try:
        from memory.memory_lancedb_pro import (
            add_memory, search_memory, get_recent_memories,
            update_memory, delete_memory, clear_short_term_memory,
            get_memory_stats
        )
        
        # 测试添加记忆
        print("1. 测试添加记忆...")
        memory_id = add_memory("测试记忆内容", memory_type="short_term", metadata={"test": "value"})
        print(f"   添加成功，记忆ID: {memory_id}")
        
        # 测试搜索记忆
        print("2. 测试搜索记忆...")
        results = search_memory("测试")
        print(f"   搜索结果数量: {len(results)}")
        if results:
            print(f"   第一个结果: {results[0]['content']}")
        
        # 测试获取最近记忆
        print("3. 测试获取最近记忆...")
        recent = get_recent_memories(hours=1, limit=5)
        print(f"   最近记忆数量: {len(recent)}")
        
        # 测试更新记忆
        print("4. 测试更新记忆...")
        updated = update_memory(memory_id, content="更新后的测试记忆内容")
        print(f"   更新成功: {updated}")
        
        # 测试再次搜索验证更新
        print("5. 测试搜索更新后的记忆...")
        updated_results = search_memory("测试")
        if updated_results:
            print(f"   更新后内容: {updated_results[0]['content']}")
        
        # 测试获取记忆统计
        print("6. 测试获取记忆统计...")
        stats = get_memory_stats()
        print(f"   记忆总数: {stats['total']}")
        print(f"   短期记忆: {stats['short_term']}")
        print(f"   长期记忆: {stats['long_term']}")
        print(f"   事实记忆: {stats['fact']}")
        
        # 测试删除记忆
        print("7. 测试删除记忆...")
        deleted = delete_memory(memory_id)
        print(f"   删除成功: {deleted}")
        
        # 测试清除短期记忆
        print("8. 测试清除短期记忆...")
        cleared = clear_short_term_memory(older_than_hours=24)
        print(f"   清除成功: {cleared}")
        
        # 再次获取统计验证
        print("9. 测试最终记忆统计...")
        final_stats = get_memory_stats()
        print(f"   最终记忆总数: {final_stats['total']}")
        
        print("\n✅ memory-lancedb-pro 测试完成！")
        return True
    except Exception as e:
        print(f"❌ memory-lancedb-pro 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_openclaw_wiki_lancedb():
    print("\n=== 测试 openclaw-wiki-lancedb ===")
    try:
        from memory.openclaw_wiki_lancedb import (
            add_wiki_entry, search_wiki, get_wiki_entry,
            update_wiki_entry, delete_wiki_entry, get_wiki_categories,
            get_wiki_tags, import_wiki_from_json, export_wiki_to_json,
            get_wiki_stats
        )
        
        # 测试添加知识库条目
        print("1. 测试添加知识库条目...")
        entry_id = add_wiki_entry(
            title="测试知识库条目",
            content="这是一个测试知识库条目的内容",
            tags=["测试", "知识库"],
            category="test",
            metadata={"author": "test"}
        )
        print(f"   添加成功，条目ID: {entry_id}")
        
        # 测试搜索知识库
        print("2. 测试搜索知识库...")
        results = search_wiki("测试")
        print(f"   搜索结果数量: {len(results)}")
        if results:
            print(f"   第一个结果标题: {results[0]['title']}")
        
        # 测试获取知识库条目
        print("3. 测试获取知识库条目...")
        entry = get_wiki_entry(entry_id)
        if entry:
            print(f"   条目标题: {entry['title']}")
            print(f"   条目内容: {entry['content']}")
        
        # 测试更新知识库条目
        print("4. 测试更新知识库条目...")
        updated = update_wiki_entry(
            entry_id, 
            title="更新后的测试知识库条目",
            content="这是更新后的内容"
        )
        print(f"   更新成功: {updated}")
        
        # 测试再次搜索验证更新
        print("5. 测试搜索更新后的知识库...")
        updated_results = search_wiki("测试")
        if updated_results:
            print(f"   更新后标题: {updated_results[0]['title']}")
        
        # 测试获取知识库分类
        print("6. 测试获取知识库分类...")
        categories = get_wiki_categories()
        print(f"   分类列表: {categories}")
        
        # 测试获取知识库标签
        print("7. 测试获取知识库标签...")
        tags = get_wiki_tags()
        print(f"   标签列表: {tags}")
        
        # 测试获取知识库统计
        print("8. 测试获取知识库统计...")
        stats = get_wiki_stats()
        print(f"   条目总数: {stats['total']}")
        print(f"   分类数量: {stats['categories']}")
        print(f"   标签数量: {stats['tags']}")
        print(f"   分类统计: {stats['category_counts']}")
        
        # 测试删除知识库条目
        print("9. 测试删除知识库条目...")
        deleted = delete_wiki_entry(entry_id)
        print(f"   删除成功: {deleted}")
        
        # 再次获取统计验证
        print("10. 测试最终知识库统计...")
        final_stats = get_wiki_stats()
        print(f"   最终条目总数: {final_stats['total']}")
        
        print("\n✅ openclaw-wiki-lancedb 测试完成！")
        return True
    except Exception as e:
        print(f"❌ openclaw-wiki-lancedb 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_integration():
    print("\n=== 测试系统集成 ===")
    try:
        from ga import get_global_memory
        
        # 测试 get_global_memory() 是否包含新记忆引擎信息
        print("1. 测试 get_global_memory() 函数...")
        global_memory = get_global_memory()
        print("   全局记忆获取成功")
        
        # 检查是否包含新记忆引擎信息
        if "Advanced Memory Engines" in global_memory:
            print("   ✅ 全局记忆包含高级记忆引擎信息")
        else:
            print("   ❌ 全局记忆不包含高级记忆引擎信息")
        
        print("\n✅ 系统集成测试完成！")
        return True
    except Exception as e:
        print(f"❌ 系统集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("开始测试记忆引擎集成...")
    
    # 安装依赖
    print("\n=== 安装依赖 ===")
    try:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "lancedb", "langchain", "openai", "langchain-openai"], check=True)
        print("✅ 依赖安装成功")
    except Exception as e:
        print(f"⚠️  依赖安装失败: {e}")
        print("继续测试，但可能会失败")
    
    # 运行测试
    results = []
    results.append("memory-lancedb-pro: " + ("✅" if test_memory_lancedb_pro() else "❌"))
    results.append("openclaw-wiki-lancedb: " + ("✅" if test_openclaw_wiki_lancedb() else "❌"))
    results.append("系统集成: " + ("✅" if test_integration() else "❌"))
    
    # 打印测试结果
    print("\n=== 测试结果汇总 ===")
    for result in results:
        print(f"  {result}")
    
    print("\n测试完成！")

if __name__ == "__main__":
    main()