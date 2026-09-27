package com.zhuzhu.update.web;

import org.springframework.core.io.AbstractResource;

import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;

/**
 * multipart/byteranges 响应体：把客户端一次请求的多个字节区间拼成多段 206 输出。
 * 分段用 RandomAccessFile 定位读取，HTTP 层通过 getInputStream 流式消费，不整读进内存。
 *
 * <p>响应流格式（RFC 9110）：每个分段 = 前缀头(含 boundary)+文件字节，末尾追加结束边界。</p>
 */
public class MultiRangeFileResource extends AbstractResource implements Closeable {

    private final Path file;
    private final long total;
    private final List<long[]> ranges;
    private final String boundary;

    public MultiRangeFileResource(Path file, List<long[]> ranges, long total, String boundary) {
        this.file = file;
        this.ranges = ranges;
        this.total = total;
        this.boundary = boundary;
    }

    private String partPrefix(int i) {
        long[] r = ranges.get(i);
        String sep = i == 0 ? "" : "\r\n";
        return sep + "--" + boundary + "\r\n"
                + "Content-Type: application/octet-stream\r\n"
                + "Content-Range: bytes " + r[0] + "-" + r[1] + "/" + total + "\r\n\r\n";
    }

    private String terminator() {
        return "\r\n--" + boundary + "--\r\n";
    }

    @Override
    public boolean exists() {
        return file.toFile().isFile();
    }

    @Override
    public long contentLength() {
        long len = 0;
        for (int i = 0; i < ranges.size(); i++) {
            len += partPrefix(i).length();
            long[] r = ranges.get(i);
            len += r[1] - r[0] + 1;
        }
        len += terminator().length();
        return len;
    }

    @Override
    public InputStream getInputStream() throws IOException {
        return new MultiRangeInputStream();
    }

    @Override
    public String getDescription() {
        return file.toAbsolutePath() + " [multipart/byteranges]";
    }

    @Override
    public void close() throws IOException {
        // 资源本身不持有关闭状态，close 由内部的 MultiRangeInputStream 管理
    }

    /** 流式读取：前缀头 → 文件字节 → 下一段前缀头 … → 结束边界 */
    private final class MultiRangeInputStream extends InputStream {
        private final RandomAccessFile raf;
        private final byte[] terminator = terminator().getBytes(StandardCharsets.US_ASCII);
        private byte[] prefix;
        private int prefixPos;
        private int partIndex = 0;
        private long bodyRemain;
        private int termPos = 0;
        private boolean eof = false;

        MultiRangeInputStream() throws IOException {
            this.raf = new RandomAccessFile(file.toFile(), "r");
            loadPrefix();
        }

        private void loadPrefix() throws IOException {
            prefix = partPrefix(partIndex).getBytes(StandardCharsets.US_ASCII);
            prefixPos = 0;
            long[] r = ranges.get(partIndex);
            raf.seek(r[0]);
            bodyRemain = r[1] - r[0] + 1;
        }

        @Override
        public void close() throws IOException {
            raf.close();
            eof = true;
        }

        @Override
        public int read() throws IOException {
            byte[] one = new byte[1];
            int n = read(one, 0, 1);
            return n < 0 ? -1 : (one[0] & 0xff);
        }

        @Override
        public int read(byte[] b, int off, int len) throws IOException {
            if (eof) return -1;
            if (len == 0) return 0;
            int filled = 0;
            while (filled < len) {
                int n = readChunk(b, off + filled, len - filled);
                if (n < 0) break;
                filled += n;
            }
            return filled == 0 ? -1 : filled;
        }

        private int readChunk(byte[] b, int off, int len) throws IOException {
            if (prefixPos < prefix.length) {
                int n = Math.min(len, prefix.length - prefixPos);
                System.arraycopy(prefix, prefixPos, b, off, n);
                prefixPos += n;
                return n;
            }
            if (bodyRemain > 0) {
                int toRead = (int) Math.min(len, bodyRemain);
                int n = raf.read(b, off, toRead);
                if (n < 0) throw new IOException("文件被截断: " + file);
                bodyRemain -= n;
                if (bodyRemain == 0 && partIndex + 1 < ranges.size()) {
                    partIndex++;
                    loadPrefix();
                }
                return n;
            }
            if (termPos < terminator.length) {
                int n = Math.min(len, terminator.length - termPos);
                System.arraycopy(terminator, termPos, b, off, n);
                termPos += n;
                if (termPos >= terminator.length) eof = true;
                return n;
            }
            eof = true;
            return -1;
        }
    }
}