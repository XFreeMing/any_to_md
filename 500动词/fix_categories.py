#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据主文件结构修复动词文件的分类路径
"""

import re
import os

def parse_main_file(main_file):
    """解析主文件，构建动词到分类路径的映射"""
    verb_to_category = {}
    
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
            current_path = [main_cat]
        # 识别子分类（A. 定边界, B. 定概念等）
        elif re.match(r'^[A-H]\.\s+', line):
            sub_cat = re.sub(r'^[A-H]\.\s+', '', line).strip()
            sub_cat = re.sub(r'\s+\d+$', '', sub_cat)  # 移除页码
            if current_path:
                current_path = [current_path[0], sub_cat]
            else:
                current_path = [sub_cat]
        # 识别详细分类（扩展边界, 描述边界等）
        elif line and re.search(r'[\u4e00-\u9fff]', line) and not line.startswith('@') and not line.startswith('>') and not line.startswith('-') and not re.match(r'^\d+\.', line) and not line.startswith('Table of Contents') and not line.startswith('Bibliography') and not line.startswith('学术英文') and not line.startswith('五百动词语境习词') and not line.startswith('例句库'):
            detail_cat = re.sub(r'\s+\d+$', '', line).strip()
            # 更新当前路径，添加详细分类
            if current_path:
                current_path = current_path + [detail_cat]
            else:
                current_path = [detail_cat]
        # 识别动词引用
        elif line.startswith('@'):
            match = re.match(r'@(\w+)\.md\s+\((\d+)-(\d+)\)', line)
            if match:
                verb_file = match.group(1)
                # 构建分类路径（使用当前路径，包括详细分类）
                if current_path:
                    if len(current_path) >= 3:
                        category_path = '/'.join(current_path)
                    elif len(current_path) == 2:
                        category_path = '/'.join(current_path)
                    else:
                        category_path = current_path[0]
                else:
                    category_path = '未分类'
                
                verb_to_category[verb_file] = category_path
        
        i += 1
    
    return verb_to_category

def fix_verb_file(verb_file, category_path):
    """修复单个动词文件的分类路径（只修复显示"未分类"的文件）"""
    if not os.path.exists(verb_file):
        return False
    
    with open(verb_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 只替换"未分类"，不替换已有的分类路径
    if '未分类' in content:
        new_content = re.sub(
            r'tags:\s*\n\s*-\s*未分类',
            f'tags:\n  - {category_path}',
            content
        )
        
        if new_content != content:
            with open(verb_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
    
    return False

def main():
    main_file = '500动词.md.new'
    
    if not os.path.exists(main_file):
        print(f"错误：找不到文件 {main_file}")
        return
    
    print(f"正在解析 {main_file}...")
    verb_to_category = parse_main_file(main_file)
    
    print(f"找到 {len(verb_to_category)} 个动词映射")
    
    fixed_count = 0
    for verb_file, category_path in verb_to_category.items():
        filename = f"{verb_file}.md"
        if fix_verb_file(filename, category_path):
            print(f"  已修复: {filename} -> {category_path}")
            fixed_count += 1
    
    print(f"\n共修复 {fixed_count} 个文件")

if __name__ == '__main__':
    main()

