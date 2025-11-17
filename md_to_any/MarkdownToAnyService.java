package com.baiying.ai.mcpplatformapi.md_to_any.service;

import com.baiying.ai.mcpplatformapi.md_to_any.MdToAnyIO;

/**
 * Markdown 转换服务接口
 */
public interface MarkdownToAnyService {
    
    /**
     * 转换 Markdown 内容
     *
     * @param mdContent Markdown 内容字符串
     * @return 转换结果
     * @throws Exception 转换过程中的异常
     */
    MdToAnyIO.Result convertMarkdown(String mdContent) throws Exception;
    
    /**
     * 转换 Markdown 内容（带参数）
     *
     * @param aiCodingId AI编码ID
     * @param mdContent  Markdown 内容字符串
     * @param fileName   文件名（不含扩展名）
     * @return 转换结果
     * @throws Exception 转换过程中的异常
     */
    MdToAnyIO.Result convertMarkdown(String aiCodingId, String mdContent, String fileName) throws Exception;
}
