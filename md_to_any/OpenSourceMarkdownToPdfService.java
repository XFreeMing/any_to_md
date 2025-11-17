package com.baiying.ai.mcpplatformapi.md_to_any.service;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

import com.baiying.ai.mcpplatformapi.common.client.attchment.AttachmentClient;
import com.baiying.ai.mcpplatformapi.md_to_any.MdToAnyIO;
import com.openhtmltopdf.pdfboxout.PdfRendererBuilder;
import com.openhtmltopdf.svgsupport.BatikSVGDrawer;

import lombok.extern.slf4j.Slf4j;

/**
 * 开源方案 Markdown 转 PDF 服务
 */
@Service
@Slf4j
public class OpenSourceMarkdownToPdfService extends AbstractMarkdownToAnyService {

    public OpenSourceMarkdownToPdfService(
            AttachmentClient attachmentClient,
            EmojiConfigLoader emojiConfigLoader,
            @Value("${md_to_any.template_url:https://smbfiletest.oss-cn-beijing.aliyuncs.com/md_to_any_template/md_to_html.html}") String templateUrl) {
        super(attachmentClient, emojiConfigLoader, templateUrl);
    }

    @Override
    public MdToAnyIO.Result convertMarkdown(String aiCodingId, String mdContent, String fileName) throws Exception {
        log.info("=== 开始开源方案 Markdown 转 PDF 流程（Emoji资源）===\n文件名: {}", fileName);

        Path tempDir = null;
        try {
            // 1. 创建临时目录
            tempDir = Files.createTempDirectory("markdown_pdf_" + System.currentTimeMillis());
            log.info("✓ 创建临时目录: {}", tempDir);

            // 2. 将 Markdown 转换为 HTML（启用Emoji扩展）
            log.info("步骤 1: 转换 Markdown 为 HTML（包含Emoji处理）...");
            String html = convertMarkdownToHtmlWithEmoji(mdContent);
            String htmlUrl = attachmentClient.uploadStringAsFile(aiCodingId, "md_to_any", html, fileName + ".html");
            log.info("✓ Markdown 转 HTML 成功，HTML 长度: {} 字符", html.length());

            // 3. 处理HTML中的Emoji，转换为内嵌的Base64图片
            log.info("步骤 2: 处理HTML中的Emoji（使用本地资源）...");
            String processedHtml = processEmojiInHtml(html);

            // 4. 处理HTML中的WebP图片，转换为PNG格式（base64）
            log.info("步骤 3: 处理HTML中的WebP图片（转换为PNG）...");
            String webpProcessedHtml = processWebPImagesInHtml(processedHtml);

            // 5. XML兼容性处理
            String xmlCompatibleHtml = sanitizeHtmlForXml(webpProcessedHtml);
            
            // 5.1. 确保使用 Microsoft YaHei 字体（通过内联样式强制设置）
            String htmlWithFont = ensureMicrosoftYaHeiFont(xmlCompatibleHtml);

            // 6. 将 HTML 转换为 PDF 字节数组
            log.info("步骤 4: 转换 HTML 为 PDF...");
            byte[] pdfBytes = convertHtmlToPdfWithColorEmoji(htmlWithFont, tempDir);
            log.info("✓ HTML 转 PDF 成功，PDF 大小: {} 字节", pdfBytes.length);

            // 7. 上传到 OSS 获取 URL
            log.info("步骤 5: 上传到 OSS...");
            String pdfFileName = fileName + ".pdf";
            String ossUrl = attachmentClient.uploadUserFile(pdfBytes, aiCodingId, "md_to_any", pdfFileName);
            log.info("✓ 上传到 OSS 成功，URL: {}", ossUrl);

            log.info("=== 开源方案 Markdown 转 PDF 流程完成 ===");
            return new MdToAnyIO.Result(ossUrl, htmlUrl);

        } catch (Exception e) {
            log.error("Markdown 转 PDF 过程中发生错误: {}", e.getMessage(), e);
            throw new RuntimeException("Markdown 转 PDF 失败: " + e.getMessage(), e);
        } finally {
            cleanupTempFiles(tempDir);
        }
    }

    /**
     * 将 HTML 转换为 PDF 字节数组（支持彩色Emoji）
     */
    private byte[] convertHtmlToPdfWithColorEmoji(String html, @SuppressWarnings("unused") Path tempDir) {
        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {

            PdfRendererBuilder builder = new PdfRendererBuilder();

            builder.withHtmlContent(html, null);
            builder.toStream(outputStream);

            // 添加SVG支持（用于渲染emoji SVG）
            try {
                builder.useSVGDrawer(new BatikSVGDrawer());
                log.debug("✓ SVG支持已启用");
            } catch (Exception svgException) {
                log.warn("SVG支持启用失败，将使用fallback: {}", svgException.getMessage());
            }

            try {
                // 加载中文字体
                loadChineseFonts(builder);
            } catch (Exception fontException) {
                log.warn("字体加载失败，将使用系统默认字体: {}", fontException.getMessage());
            }

            // 设置PDF生成选项
            builder.useFastMode();

            try {
                builder.run();
                log.info("✓ PDF生成成功，包含彩色Emoji支持（使用本地静态资源）");
            } catch (Exception pdfException) {
                
                // 降级处理：移除所有 img 标签（用 alt 文本或空字符串替换）
                // 先移除 WebP 图片（如果存在）
                java.util.regex.Pattern webpImgPattern = java.util.regex.Pattern.compile(
                    "<img\\s+[^>]*?src\\s*=\\s*[\"'][^\"']*?\\.webp[^\"']*?[\"'][^>]*?>(?:(?:(?!</img>).)*</img>)?",
                    java.util.regex.Pattern.CASE_INSENSITIVE | java.util.regex.Pattern.DOTALL
                );
                String fallbackHtml = webpImgPattern.matcher(html).replaceAll(matchResult -> {
                    String imgTag = matchResult.group();
                    java.util.regex.Pattern altPattern = java.util.regex.Pattern.compile(
                        "alt\\s*=\\s*[\"']([^\"']*?)[\"']", 
                        java.util.regex.Pattern.CASE_INSENSITIVE
                    );
                    java.util.regex.Matcher altMatcher = altPattern.matcher(imgTag);
                    return altMatcher.find() ? altMatcher.group(1) : "";
                });
                // 再移除其他图片标签（用 alt 文本替换）
                fallbackHtml = fallbackHtml.replaceAll("<img[^>]*?alt=\"([^\"]+)\"[^>]*?>", "$1");
                fallbackHtml = removeResidualEmojiControlChars(fallbackHtml);

                try (ByteArrayOutputStream fallbackStream = new ByteArrayOutputStream()) {
                    PdfRendererBuilder fallbackBuilder = new PdfRendererBuilder();
                    fallbackBuilder.withHtmlContent(fallbackHtml, null);
                    fallbackBuilder.toStream(fallbackStream);
                    fallbackBuilder.useFastMode();

                    loadChineseFonts(fallbackBuilder);

                    fallbackBuilder.run();
                    log.info("✓ 降级处理成功");
                    return fallbackStream.toByteArray();
                } catch (Exception fallbackException) {
                    log.error("降级处理失败: {}", fallbackException.getMessage());
                    throw new RuntimeException("PDF生成失败", fallbackException);
                }
            }

            return outputStream.toByteArray();

        } catch (Exception e) {
            log.error("HTML 转 PDF 失败: {}", e.getMessage(), e);
            throw new RuntimeException("HTML 转 PDF 失败", e);
        }
    }


    /**
     * 确保 HTML 使用 Microsoft YaHei 字体
     * 通过修改 CSS 或添加内联样式来实现
     */
    private String ensureMicrosoftYaHeiFont(String html) {
        // 方法1: 如果 HTML 中有 <style> 标签，更新 font-family
        String fontFamilyCss = "'Microsoft YaHei', '微软雅黑', 'SimSun', '宋体', serif, sans-serif";
        String codeFontFamilyCss = "'Microsoft YaHei', '微软雅黑', 'SimSun', '宋体', monospace";
        
        // 替换 body 的 font-family
        html = html.replaceAll(
            "(?i)(body\\s*\\{[^}]*font-family\\s*:\\s*)([^;]+)([^}]*\\})",
            "$1" + fontFamilyCss + "$3"
        );
        
        // 替换所有其他元素的 font-family（如果有）
        html = html.replaceAll(
            "(?i)font-family\\s*:\\s*['\"]?SimSun['\"]?",
            "font-family: " + fontFamilyCss
        );
        
        // 确保代码块也使用支持中文的字体
        html = html.replaceAll(
            "(?i)(code\\s*\\{[^}]*font-family\\s*:\\s*)([^;]+)([^}]*\\})",
            "$1" + codeFontFamilyCss + "$3"
        );
        html = html.replaceAll(
            "(?i)(pre\\s*\\{[^}]*font-family\\s*:\\s*)([^;]+)([^}]*\\})",
            "$1" + codeFontFamilyCss + "$3"
        );
        
        // 方法2: 在 body 标签上添加内联样式（如果还没有 style 属性）
        if (html.contains("<body") && !html.matches("(?i).*<body[^>]*style\\s*=")) {
            html = html.replaceFirst(
                "(?i)(<body[^>]*?)(>)",
                "$1 style=\"font-family: " + fontFamilyCss + ";\"$2"
            );
        }
        
        log.debug("已确保 HTML 使用 Microsoft YaHei 字体（包括代码块）");
        return html;
    }

    /**
     * 加载中文字体
     * 优先设置 Microsoft YaHei 为默认字体
     * 
     * TTC vs TTF 说明：
     * - TTF (TrueType Font): 单个字体文件，包含一个字体的所有信息
     * - TTC (TrueType Collection): 字体集合文件，包含多个字体变体（如 Regular, Bold, Light）
     * openhtmltopdf 支持直接加载 TTC 文件，会自动选择合适的字体变体
     */
    private void loadChineseFonts(PdfRendererBuilder builder) {
        log.info("开始加载中文字体...");
        boolean fontLoaded = false;

        // 1. 优先尝试加载微软雅黑.ttc（主字体文件）
        try {
            ClassPathResource yaHeiFont = new ClassPathResource("fonts/微软雅黑.ttc");
            if (yaHeiFont.exists()) {
                Path tempFontFile = Files.createTempFile("yahei_", ".ttc");
                try (InputStream fontStream = yaHeiFont.getInputStream()) {
                    Files.copy(fontStream, tempFontFile, StandardCopyOption.REPLACE_EXISTING);

                    // 注册 Microsoft YaHei（作为默认字体）
                    loadFontSafely(builder, tempFontFile.toFile(), "Microsoft YaHei", "微软雅黑字体（主字体）");
                    loadFontSafely(builder, tempFontFile.toFile(), "微软雅黑", null);
                    
                    log.info("✓ 成功加载微软雅黑.ttc，默认字体：Microsoft YaHei");
                    fontLoaded = true;
                } finally {
                    // 注意：临时文件会在 PDF 生成完成后由 cleanupTempFiles 清理
                }
            }
        } catch (Exception e) {
            log.warn("加载微软雅黑.ttc 失败: {}", e.getMessage());
        }

        // 2. 尝试加载微软雅黑 Light.ttc（Light 变体）
        try {
            ClassPathResource yaHeiLightFont = new ClassPathResource("fonts/微软雅黑 Light.ttc");
            if (yaHeiLightFont.exists()) {
                Path tempFontFile = Files.createTempFile("yahei_light_", ".ttc");
                try (InputStream fontStream = yaHeiLightFont.getInputStream()) {
                    Files.copy(fontStream, tempFontFile, StandardCopyOption.REPLACE_EXISTING);

                    // 注册 Light 变体
                    loadFontSafely(builder, tempFontFile.toFile(), "Microsoft YaHei Light", "微软雅黑 Light");
                    
                    log.info("✓ 成功加载微软雅黑 Light.ttc");
                } finally {
                    // 临时文件会在后续清理
                }
            }
        } catch (Exception e) {
            log.warn("加载微软雅黑 Light.ttc 失败: {}", e.getMessage());
        }

        // 3. 如果微软雅黑字体未加载成功，使用黑体字体作为后备
        if (!fontLoaded) {
            try {
                ClassPathResource heiTiFont = new ClassPathResource("fonts/黑体.ttf");
                if (heiTiFont.exists()) {
                    Path tempFontFile = Files.createTempFile("heiti_", ".ttf");
                    try (InputStream fontStream = heiTiFont.getInputStream()) {
                        Files.copy(fontStream, tempFontFile, StandardCopyOption.REPLACE_EXISTING);

                        // 注册 Microsoft YaHei（作为后备）
                        loadFontSafely(builder, tempFontFile.toFile(), "Microsoft YaHei", "微软雅黑字体（后备：黑体）");
                        loadFontSafely(builder, tempFontFile.toFile(), "微软雅黑", null);
                        
                        // 注册其他字体作为后备
                        loadFontSafely(builder, tempFontFile.toFile(), "SimSun", "黑体字体");
                        loadFontSafely(builder, tempFontFile.toFile(), "宋体", null);

                        log.info("✓ 成功加载黑体字体（作为后备），默认字体：Microsoft YaHei");
                    }
                }
            } catch (Exception e) {
                log.warn("加载黑体字体失败: {}", e.getMessage());
            }
        }
    }

    /**
     * 安全地加载字体，抑制警告信息
     */
    private void loadFontSafely(PdfRendererBuilder builder, java.io.File fontFile, String fontFamily,
            String description) {
        try {
            // 临时重定向System.err来抑制警告
            PrintStream originalErr = System.err;
            ByteArrayOutputStream suppressedOutput = new ByteArrayOutputStream();
            System.setErr(new PrintStream(suppressedOutput));

            try {
                builder.useFont(fontFile, fontFamily);
                if (description != null) {
                    log.debug("✓ 成功注册字体: {} -> {}", fontFamily, description);
                }
            } finally {
                // 恢复System.err
                System.setErr(originalErr);
            }

        } catch (Exception e) {
            log.warn("注册字体失败 {}: {}", fontFamily, e.getMessage());
        }
    }
}

