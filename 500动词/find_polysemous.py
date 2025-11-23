#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
找出所有有"一词多义"说明的动词
"""

import re

polysemous_verbs = {}

with open('500动词.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

current_verb = None
meanings = []

for i, line in enumerate(lines):
    line_stripped = line.strip()
    
    # 识别动词引用
    if line_stripped.startswith('@'):
        match = re.match(r'@(\w+)\.md', line_stripped)
        if match:
            current_verb = match.group(1)
            meanings = []
    
    # 识别"一词多义"标记（特殊字符）
    if '致力于' in line_stripped or '申请' in line_stripped or '告示' in line_stripped or '告知' in line_stripped or '以某种方式表达' in line_stripped or '标明' in line_stripped or '表示' in line_stripped or '将…移动' in line_stripped or '引导开' in line_stripped or '增加' in line_stripped or '促进' in line_stripped or '提高' in line_stripped or '鼓励' in line_stripped or '支持' in line_stripped or '增进' in line_stripped or '提升' in line_stripped or '困惑' in line_stripped or '混淆' in line_stripped or '遵守' in line_stripped or '遵从' in line_stripped or '认为' in line_stripped or '疏于照管' in line_stripped or '照顾' in line_stripped or '照料' in line_stripped or '值得' in line_stripped or '维持' in line_stripped or '争论' in line_stripped or '争吵' in line_stripped or '使某事基于某事' in line_stripped:
        # 提取含义说明
        if current_verb:
            # 尝试从行中提取含义
            meaning_match = re.search(r'[：:]\s*([^。，\n]+)', line_stripped)
            if meaning_match:
                meaning = meaning_match.group(1).strip()
            else:
                # 如果没有冒号，尝试提取整行（去除特殊字符）
                meaning = re.sub(r'^[^\u4e00-\u9fff]*', '', line_stripped).strip()
                meaning = re.sub(r'\s+\d+\.', '', meaning).strip()
            
            if meaning and current_verb not in polysemous_verbs:
                polysemous_verbs[current_verb] = []
            if meaning:
                polysemous_verbs[current_verb].append(meaning)

# 更精确地查找
with open('500动词.md', 'r', encoding='utf-8') as f:
    content = f.read()
    
# 查找所有带特殊标记的行
for match in re.finditer(r'@(\w+)\.md.*?\n(?:[^\n]*\n)*?[^\u0000-\u007F\u00A0-\uFFFF]*([^\n]+)', content, re.MULTILINE):
    verb = match.group(1)
    meaning_line = match.group(2).strip()
    if '致力于' in meaning_line or '申请' in meaning_line or '告示' in meaning_line or '告知' in meaning_line:
        if verb not in polysemous_verbs:
            polysemous_verbs[verb] = []
        # 提取含义
        meaning = re.sub(r'^[^\u4e00-\u9fff]*', '', meaning_line).strip()
        if meaning:
            polysemous_verbs[verb].append(meaning)

if polysemous_verbs:
    print(f'发现 {len(polysemous_verbs)} 个有多个含义说明的动词：\n')
    for verb, meanings_list in sorted(polysemous_verbs.items()):
        print(f'{verb}:')
        for meaning in meanings_list:
            print(f'  - {meaning}')
        print()
else:
    print('未发现有一词多义说明的动词')

