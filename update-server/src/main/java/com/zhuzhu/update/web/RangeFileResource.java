package com.zhuzhu.update.web;

import org.springframework.core.io.AbstractResource;

import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.file.Path;

/**
 * 支持断点续传的 Resource 实现
 * 读取文件的指定范围，用于 HTTP 206 Partial Content 响应
 */
public class RangeFileResource extends AbstractResource {

    private final Path file;
    private final long start;
    private final long length;

    public RangeFileResource(Path file, long start, long length) {
        this.file = file;
        this.start = start;
        this.length = length;
    }

    @Override
    public InputStream getInputStream() throws IOException {
        RandomAccessFile raf = new RandomAccessFile(file.toFile(), "r");
        raf.seek(start);
        return new RangeInputStream(raf, length);
    }

    @Override
    public long contentLength() {
        return length;
    }

    @Override
    public String getDescription() {
        return file.toAbsolutePath() + " [bytes " + start + "-" + (start + length - 1) + "]";
    }

    /**
     * 限制读取字节数的 InputStream 包装器
     */
    static class RangeInputStream extends InputStream {
        private final RandomAccessFile raf;
        private final long total;
        private long read = 0;

        RangeInputStream(RandomAccessFile raf, long total) {
            this.raf = raf;
            this.total = total;
        }

        @Override
        public int read() throws IOException {
            if (read >= total) {
                return -1;
            }
            int b = raf.read();
            if (b >= 0) {
                read++;
            }
            return b;
        }

        @Override
        public int read(byte[] b, int off, int len) throws IOException {
            if (read >= total) {
                return -1;
            }
            int toRead = (int) Math.min(len, total - read);
            int n = raf.read(b, off, toRead);
            if (n > 0) {
                read += n;
            }
            return n;
        }

        @Override
        public void close() throws IOException {
            raf.close();
        }
    }
}
