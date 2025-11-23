#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为有"一词多义"说明的动词更新tags，添加多个含义标签
"""

import re
import os

# 一词多义动词及其含义（从主文件中提取）
polysemous_meanings = {
    'address': ['致力于某事'],
    'apply': ['申请'],
    'inform': ['告示；告知'],
    'render': ['以某种方式表达；表现；使成为'],
    'designate': ['标明；表示'],
    'displace': ['将…移动；引导开'],
    'multiply': ['增加'],
    'promote': ['促进，提高', '鼓励，支持'],
    'advance': ['增进；提升'],
    'confound': ['困惑；混淆'],
    'observe': ['遵守，遵从'],
    'view': ['认为'],
    'neglect': ['疏于照管'],
    'tend': ['照顾；照料'],
    'warrant': ['值得'],
    'maintain': ['维持'],
    'argue': ['争论，争吵'],
    'predicate': ['使某事基于某事'],
}

def update_verb_file(verb_file, meanings):
    """为动词文件添加多个含义标签"""
    if not os.path.exists(verb_file):
        return False
    
    with open(verb_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 读取现有的tags
    tags_match = re.search(r'tags:\s*\n((?:\s*-\s*[^\n]+\n)*)', content)
    if not tags_match:
        return False
    
    existing_tags = tags_match.group(1)
    
    # 添加含义标签
    new_tags = existing_tags
    for meaning in meanings:
        # 检查是否已存在
        if meaning not in existing_tags:
            new_tags += f'  - 含义: {meaning}\n'
    
    # 替换tags部分
    new_content = re.sub(
        r'tags:\s*\n((?:\s*-\s*[^\n]+\n)*)',
        f'tags:\n{new_tags}',
        content
    )
    
    if new_content != content:
        with open(verb_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    
    return False

def main():
    updated_count = 0
    
    print("正在更新一词多义动词的tags...\n")
    
    for verb, meanings in sorted(polysemous_meanings.items()):
        filename = f"{verb}.md"
        if update_verb_file(filename, meanings):
            print(f"  ✓ {filename}: 添加了 {len(meanings)} 个含义标签")
            for meaning in meanings:
                print(f"    - {meaning}")
            updated_count += 1
        else:
            print(f"  ✗ {filename}: 未找到或已存在")
    
    print(f"\n共更新 {updated_count} 个文件")

if __name__ == '__main__':
    main()

