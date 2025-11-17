package com.baiying.ai.mcpplatformapi.md_to_any.service;

import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.stereotype.Component;

import lombok.extern.slf4j.Slf4j;

/**
 * Emoji配置加载器 - 本地模式（智能映射版）
 * 负责加载和管理Emoji相关的配置信息，使用本地静态资源
 */
@Component
@Slf4j
public class EmojiConfigLoader {

    private final Map<String, String> hotEmojiCache;
    private final Set<String> availableSvgCodePoints;
    private final String emojiRegexPattern;
    private final String defaultImgStyle;
    private final String localBaseUrl;

    public EmojiConfigLoader() {
        // 使用本地静态资源路径
        this.localBaseUrl = "/static/twemoji/assets/svg/";
        this.availableSvgCodePoints = initializeAvailableSvgCodePoints();
        this.hotEmojiCache = initializeHotEmojiCache();
        this.emojiRegexPattern = buildOptimizedEmojiRegex();
        this.defaultImgStyle = "width: 1.2em; height: 1.2em; vertical-align: middle; display: inline-block; margin: 0 0.1em;";
        log.info("EmojiConfigLoader initialized with {} hot emoji cache, {} available SVG files (Smart Mapping Mode)", 
                hotEmojiCache.size(), availableSvgCodePoints.size());
    }

    /**
     * 构建Emoji正则表达式
     */
    public String buildEmojiRegex() {
        return emojiRegexPattern;
    }

    /**
     * 获取Emoji对应的本地SVG URL（智能映射）
     */
    public String getEmojiSvgUrl(String emoji) {
        // 1. 首先检查热点缓存
        String cachedUrl = hotEmojiCache.get(emoji);
        if (cachedUrl != null) {
            return cachedUrl;
        }

        // 2. 尝试多种策略找到匹配的SVG文件
        String codePoint = findMatchingSvgCodePoint(emoji);
        if (codePoint == null) {
            // 如果找不到匹配的SVG，生成代码点但不缓存（因为文件可能不存在）
            codePoint = convertEmojiToCodePoint(emoji);
            log.debug("Emoji [{}] 未在可用SVG文件中找到匹配，生成代码点: {} (文件可能不存在)", emoji, codePoint);
        } else {
            // 找到匹配的SVG文件，缓存结果以提升性能
            String svgPath = localBaseUrl + codePoint + ".svg";
            hotEmojiCache.put(emoji, svgPath);
            return svgPath;
        }

        // 3. 即使找不到匹配的SVG，也返回生成的代码点路径（让调用者检查文件是否存在）
        return localBaseUrl + codePoint + ".svg";
    }

    /**
     * 尝试多种策略找到匹配的SVG代码点
     */
    private String findMatchingSvgCodePoint(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        // 策略1: 完整的代码点（包含肤色修饰符）
        String fullCodePoint = convertEmojiToCodePoint(emoji);
        if (availableSvgCodePoints.contains(fullCodePoint)) {
            return fullCodePoint;
        }

        // 策略2: 移除肤色修饰符但保留FE0F（变体选择器）
        String withoutSkinTone = convertEmojiToCodePointWithoutSkinTone(emoji);
        if (availableSvgCodePoints.contains(withoutSkinTone)) {
            return withoutSkinTone;
        }

        // 策略3: 移除所有修饰符（肤色、ZWJ、FE0F），只保留基础代码点
        String baseCodePoint = convertEmojiToBaseCodePoint(emoji);
        if (availableSvgCodePoints.contains(baseCodePoint)) {
            return baseCodePoint;
        }

        // 策略4: 处理ZWJ序列 - 保留ZWJ但移除FE0F
        String withZwj = convertEmojiToCodePointWithZwj(emoji);
        if (availableSvgCodePoints.contains(withZwj)) {
            return withZwj;
        }

        // 策略5: 尝试只取第一个代码点（对于复杂序列）
        String firstCodePoint = getFirstCodePoint(emoji);
        if (firstCodePoint != null && availableSvgCodePoints.contains(firstCodePoint)) {
            return firstCodePoint;
        }

        return null;
    }

    /**
     * 生成图片样式
     */
    public String generateImgStyle() {
        return defaultImgStyle;
    }

    /**
     * 初始化可用的SVG代码点集合（扫描目录中的所有SVG文件）
     */
    private Set<String> initializeAvailableSvgCodePoints() {
        Set<String> codePoints = new HashSet<>();
        
        try {
            PathMatchingResourcePatternResolver resolver = new PathMatchingResourcePatternResolver();
            Resource[] resources = resolver.getResources("classpath:static/twemoji/assets/svg/*.svg");
            
            for (Resource resource : resources) {
                String filename = resource.getFilename();
                if (filename != null && filename.endsWith(".svg")) {
                    // 移除 .svg 扩展名，获取代码点
                    String codePoint = filename.substring(0, filename.length() - 4);
                    codePoints.add(codePoint);
                }
            }
            
            log.debug("成功加载 {} 个SVG文件的代码点", codePoints.size());
        } catch (IOException e) {
            log.warn("扫描SVG目录失败，将使用动态映射: {}", e.getMessage());
            // 如果扫描失败，返回空集合，依赖动态生成
        }
        
        return Collections.unmodifiableSet(codePoints);
    }

    /**
     * 初始化热点Emoji缓存（仅保留高频使用的emoji）
     */
    private Map<String, String> initializeHotEmojiCache() {
        Map<String, String> cache = new HashMap<>();

        // 只缓存最常用的20个emoji，其他的动态生成
        String[][] hotEmojis = {
            // 基础表情（最高频）
            {"😀", "1f600"}, {"😁", "1f601"}, {"😂", "1f602"}, {"🤣", "1f923"},
            {"😊", "1f60a"}, {"😍", "1f60d"}, {"😘", "1f618"}, {"🥰", "1f970"},
            {"😭", "1f62d"}, {"😢", "1f622"}, {"🤔", "1f914"}, {"😅", "1f605"},

            // 手势（高频）
            {"👍", "1f44d"}, {"👎", "1f44e"}, {"👌", "1f44c"}, {"🙏", "1f64f"},
            {"👏", "1f44f"}, {"🤝", "1f91d"},

            // 心形（高频）
            {"❤️", "2764"}, {"💛", "1f49b"}
        };

        for (String[] emojiPair : hotEmojis) {
            cache.put(emojiPair[0], localBaseUrl + emojiPair[1] + ".svg");
        }

        return cache;
    }

    /**
     * 智能转换Emoji为Unicode代码点（完整版本，包含所有修饰符）
     */
    private String convertEmojiToCodePoint(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return "1f600"; // 默认笑脸
        }

        StringBuilder result = new StringBuilder();
        int[] codePoints = emoji.codePoints().toArray();

        for (int codePoint : codePoints) {
            // 跳过变体选择器（FE0F/FE0E），但保留其他修饰符
            if (isVariationSelector(codePoint)) {
                continue;
            }

            // 保留零宽连接符（ZWJ）用于序列emoji
            if (isZeroWidthJoiner(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%04x", codePoint));
                continue;
            }

            // 处理肤色修饰符
            if (isSkinToneModifier(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%x", codePoint).toLowerCase());
                continue;
            }

            // 处理普通emoji代码点和符号（包括箭头等）
            // 只要不是控制字符，都应该处理（因为正则表达式已经匹配到了它们）
            if (isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%x", codePoint).toLowerCase());
            }
        }

        return !result.isEmpty() ? result.toString() : "1f600";
    }

    /**
     * 转换emoji为代码点，移除肤色修饰符但保留其他
     */
    private String convertEmojiToCodePointWithoutSkinTone(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        StringBuilder result = new StringBuilder();
        int[] codePoints = emoji.codePoints().toArray();

        for (int codePoint : codePoints) {
            // 跳过变体选择器和肤色修饰符
            if (isVariationSelector(codePoint) || isSkinToneModifier(codePoint)) {
                continue;
            }

            // 保留零宽连接符
            if (isZeroWidthJoiner(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%04x", codePoint));
                continue;
            }

            // 处理普通emoji代码点和符号
            if (isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%x", codePoint).toLowerCase());
            }
        }

        return !result.isEmpty() ? result.toString() : null;
    }

    /**
     * 转换为基础代码点，移除所有修饰符（肤色、ZWJ、FE0F等）
     */
    private String convertEmojiToBaseCodePoint(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        StringBuilder result = new StringBuilder();
        int[] codePoints = emoji.codePoints().toArray();

        for (int codePoint : codePoints) {
            // 跳过所有修饰符和控制字符
            if (isVariationSelector(codePoint) || isZeroWidthJoiner(codePoint) || isSkinToneModifier(codePoint)) {
                continue;
            }

            // 只保留基础emoji代码点和符号
            if (isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%x", codePoint).toLowerCase());
            }
        }

        return !result.isEmpty() ? result.toString() : null;
    }

    /**
     * 转换emoji为代码点，保留ZWJ但移除FE0F
     */
    private String convertEmojiToCodePointWithZwj(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        StringBuilder result = new StringBuilder();
        int[] codePoints = emoji.codePoints().toArray();

        for (int codePoint : codePoints) {
            // 跳过变体选择器，但保留ZWJ
            if (isVariationSelector(codePoint)) {
                continue;
            }

            // 保留零宽连接符
            if (isZeroWidthJoiner(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%04x", codePoint));
                continue;
            }

            // 跳过肤色修饰符
            if (isSkinToneModifier(codePoint)) {
                continue;
            }

            // 处理普通emoji代码点和符号
            if (isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint)) {
                if (!result.isEmpty()) {
                    result.append("-");
                }
                result.append(String.format("%x", codePoint).toLowerCase());
            }
        }

        return !result.isEmpty() ? result.toString() : null;
    }

    /**
     * 获取emoji的第一个代码点
     */
    private String getFirstCodePoint(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        int[] codePoints = emoji.codePoints().toArray();
        for (int codePoint : codePoints) {
            if (!isVariationSelector(codePoint) && !isZeroWidthJoiner(codePoint) && !isSkinToneModifier(codePoint)) {
                if (isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint)) {
                    return String.format("%x", codePoint).toLowerCase();
                }
            }
        }

        return null;
    }

    /**
     * 构建优化的Emoji正则表达式（更精确）
     * 仅匹配Unicode emoji字符
     */
    private String buildOptimizedEmojiRegex() {
        // Unicode emoji字符的正则
        return "(?:[\\u2700-\\u27bf]|(?:[\\ud83c][\\udde6-\\uddff]){2}|[\\ud800-\\udbff][\\udc00-\\udfff]|[\\u0023-\\u0039]\\ufe0f?\\u20e3|\\u3299|\\u3297|\\u303d|\\u3030|\\u24c2|[\\ud83c][\\udd70-\\udd71]|[\\ud83c][\\udd7e-\\udd7f]|[\\ud83c]\\udd8e|[\\ud83c][\\udd91-\\udd9a]|[\\ud83c][\\udde6-\\uddff]|[\\ud83c][\\ude01-\\ude02]|\\ud83c\\ude1a|\\ud83c\\ude2f|[\\ud83c][\\ude32-\\ude3a]|[\\ud83c][\\ude50-\\ude51]|\\u203c|\\u2049|[\\u25aa-\\u25ab]|\\u25b6|\\u25c0|[\\u25fb-\\u25fe]|\\u00a9|\\u00ae|\\u2122|\\u2139|\\ud83c\\udc04|[\\u2600-\\u26FF]|\\u2b05|\\u2b06|\\u2b07|\\u2b1b|\\u2b1c|\\u2b50|\\u2b55|\\u231a|\\u231b|\\u2328|\\u23cf|[\\u23e9-\\u23f3]|[\\u23f8-\\u23fa]|\\ud83c\\udccf|\\u2934|\\u2935|[\\u2190-\\u21ff])";
    }

    /**
     * 判断是否为变体选择器
     */
    private boolean isVariationSelector(int codePoint) {
        return (codePoint >= 0xFE00 && codePoint <= 0xFE0F) ||
               (codePoint >= 0xE0100 && codePoint <= 0xE01EF);
    }

    /**
     * 判断是否为零宽连接符
     */
    private boolean isZeroWidthJoiner(int codePoint) {
        return codePoint == 0x200D;
    }

    /**
     * 判断是否为肤色修饰符
     */
    private boolean isSkinToneModifier(int codePoint) {
        return codePoint >= 0x1F3FB && codePoint <= 0x1F3FF;
    }

    /**
     * 判断是否为emoji代码点
     */
    private boolean isEmojiCodePoint(int codePoint) {
        return (codePoint >= 0x1F600 && codePoint <= 0x1F64F) || // 表情符号
               (codePoint >= 0x1F300 && codePoint <= 0x1F5FF) || // 杂项符号
               (codePoint >= 0x1F680 && codePoint <= 0x1F6FF) || // 交通和地图符号
               (codePoint >= 0x1F1E6 && codePoint <= 0x1F1FF) || // 区域指示符号
               (codePoint >= 0x2600 && codePoint <= 0x26FF) ||   // 杂项符号
               (codePoint >= 0x2700 && codePoint <= 0x27BF) ||   // 装饰符号
               (codePoint >= 0x1F900 && codePoint <= 0x1F9FF) || // 补充符号和象形文字
               (codePoint >= 0x1FA70 && codePoint <= 0x1FAFF);   // 扩展A符号
    }

    /**
     * 判断是否为文本符号（如数字、字母等可用于emoji组合的符号）
     */
    private boolean isTextSymbol(int codePoint) {
        // 包括数字 0-9 和字母 A-Z（用于键帽emoji）
        return (codePoint >= 0x0023 && codePoint <= 0x0039) || // #-9
               (codePoint >= 0x24C2 && codePoint <= 0x24C2) ||   // Ⓜ
               (codePoint >= 0x3297 && codePoint <= 0x3299) ||  // 日文符号
               (codePoint >= 0x3030 && codePoint <= 0x303D);   // 波浪线等符号
    }

    /**
     * 判断是否为箭头或其他符号（匹配正则表达式中包含的范围）
     * 包括箭头符号（2190-21FF）、其他Unicode符号等
     */
    private boolean isArrowOrSymbol(int codePoint) {
        // 箭头符号范围（2190-21FF）- 与正则表达式中的 [\u2190-\u21ff] 对应
        return (codePoint >= 0x2190 && codePoint <= 0x21FF) ||
               // 其他可能被正则匹配的符号
               (codePoint >= 0x203C && codePoint <= 0x2049) ||   // 203c, 2049
               (codePoint >= 0x2122 && codePoint <= 0x2139) ||   // 2122, 2139
               (codePoint >= 0x231A && codePoint <= 0x23CF) ||   // 231a-231b, 2328, 23cf
               (codePoint >= 0x23E9 && codePoint <= 0x23F3) ||   // 23e9-23f3
               (codePoint >= 0x23F8 && codePoint <= 0x23FA) ||   // 23f8-23fa
               (codePoint >= 0x2934 && codePoint <= 0x2935) ||  // 2934, 2935
               (codePoint >= 0x2B05 && codePoint <= 0x2B07) ||   // 2b05-2b07
               (codePoint >= 0x2B1B && codePoint <= 0x2B1C) ||   // 2b1b-2b1c
               (codePoint >= 0x2B50 && codePoint <= 0x2B55);    // 2b50, 2b55
    }








}
