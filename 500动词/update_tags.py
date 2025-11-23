#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据主文件结构更新所有动词文件的tags，处理一词多义情况
"""

import re
import os
from collections import defaultdict

def parse_main_file(main_file):
    """解析主文件，构建动词到分类路径的映射（支持一词多义）"""
    verb_to_categories = defaultdict(list)  # 一个动词可能有多个分类
    
    with open(main_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    current_path = []
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # 识别主要分类（I. 界定, II. 位置变化等）
        if re.match(r'^[IVX]+\.\s+', line):
            main_cat = re.sub(r'^[IVX]+\.\s+', '', line).strip()
            main_cat = re.sub(r'\s+\d+$', '', main_cat)  # 移除页码
            main_cat = re.sub(r'^，\s*', '', main_cat)  # 移除开头的逗号
            current_path = [main_cat]
            last_detail = None  # 重置详细分类
        # 识别子分类（A. 定边界, B. 定概念等）
        elif re.match(r'^[A-H]\.\s+', line):
            sub_cat = re.sub(r'^[A-H]\.\s+', '', line).strip()
            sub_cat = re.sub(r'\s+\d+$', '', sub_cat)  # 移除页码
            if current_path:
                current_path = [current_path[0], sub_cat]
            else:
                current_path = [sub_cat]
            last_detail = None  # 重置详细分类
        # 识别详细分类（扩展边界, 描述边界等）- 只保留最后一个详细分类
        elif line and re.search(r'[\u4e00-\u9fff]', line) and not line.startswith('@') and not line.startswith('>') and not line.startswith('-') and not re.match(r'^\d+\.', line) and not line.startswith('Table of Contents') and not line.startswith('Bibliography') and not line.startswith('学术英文') and not line.startswith('五百动词语境习词') and not line.startswith('例句库') and not line.startswith(''):
            detail_cat = re.sub(r'\s+\d+$', '', line).strip()
            # 只保留最后一个详细分类（覆盖之前的）
            last_detail = detail_cat
        # 识别动词引用
        elif line.startswith('@'):
            match = re.match(r'@(\w+)\.md\s+\((\d+)-(\d+)\)', line)
            if match:
                verb_file = match.group(1)
                # 构建分类路径
                if current_path:
                    # 如果有详细分类，添加到路径
                    if last_detail:
                        full_path = current_path + [last_detail]
                    else:
                        full_path = current_path
                    
                    # 构建最终路径（最多3级：主分类/子分类/详细分类）
                    if len(full_path) >= 3:
                        category_path = '/'.join(full_path)
                    elif len(full_path) == 2:
                        category_path = '/'.join(full_path)
                    else:
                        category_path = full_path[0]
                else:
                    category_path = '未分类'
                
                # 添加到列表（支持一词多义）
                verb_to_categories[verb_file].append(category_path)
        
        i += 1
    
    return verb_to_categories

def update_verb_file(verb_file, category_paths):
    """更新单个动词文件的分类路径（支持多标签）"""
    if not os.path.exists(verb_file):
        return False
    
    with open(verb_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 去重并排序
    unique_paths = sorted(list(set(category_paths)))
    
    # 构建新的tags部分
    new_tags = 'tags:\n'
    for path in unique_paths:
        new_tags += f'  - {path}\n'
    
    # 替换tags部分
    new_content = re.sub(
        r'tags:\s*\n(?:\s*-\s*[^\n]+\n)*',
        new_tags,
        content
    )
    
    if new_content != content:
        with open(verb_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    
    return False

def main():
    main_file = '500动词.md'
    
    if not os.path.exists(main_file):
        print(f"错误：找不到文件 {main_file}")
        return
    
    print(f"正在解析 {main_file}...")
    verb_to_categories = parse_main_file(main_file)
    
    print(f"找到 {len(verb_to_categories)} 个动词映射")
    
    # 统计一词多义的情况
    polysemous = {k: v for k, v in verb_to_categories.items() if len(v) > 1}
    if polysemous:
        print(f"\n发现 {len(polysemous)} 个一词多义的动词：")
        for verb, paths in sorted(polysemous.items()):
            print(f"  {verb}: {len(paths)} 个含义")
            for path in paths:
                print(f"    - {path}")
    
    fixed_count = 0
    for verb_file, category_paths in verb_to_categories.items():
        filename = f"{verb_file}.md"
        if update_verb_file(filename, category_paths):
            if len(category_paths) > 1:
                print(f"  已更新（多义）: {filename} -> {len(set(category_paths))} 个分类")
            fixed_count += 1
    
    print(f"\n共更新 {fixed_count} 个文件")
    if polysemous:
        print(f"其中 {len(polysemous)} 个文件有多个分类标签")

if __name__ == '__main__':
    main()

