package com.zhuzhu.update.service;

import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.repo.AppVersionRepo;
import org.springframework.stereotype.Service;

import java.util.List;

/** 版本与更新检查 */
@Service
public class UpdateService {

    private final AppVersionRepo versionRepo;

    public UpdateService(AppVersionRepo versionRepo) {
        this.versionRepo = versionRepo;
    }

    /** 最新版本（创建时间最新的记录） */
    public AppVersion latest() {
        return versionRepo.findFirstByOrderByCreatedAtDesc().orElse(null);
    }

    /** 全部版本按语义版本号从高到低排序 */
    public List<AppVersion> allSorted() {
        List<AppVersion> all = versionRepo.findAll();
        all.sort((a, b) -> compare(a.getVersion(), b.getVersion()));
        return all;
    }

    /** 语义化版本比较：1.2.10 > 1.2.9 */
    public static int compare(String a, String b) {
        String[] pa = (a == null ? "0" : a).split("\\.");
        String[] pb = (b == null ? "0" : b).split("\\.");
        int n = Math.max(pa.length, pb.length);
        for (int i = 0; i < n; i++) {
            int x = i < pa.length ? parseInt(pa[i]) : 0;
            int y = i < pb.length ? parseInt(pb[i]) : 0;
            if (x != y) {
                return Integer.compare(x, y);
            }
        }
        return 0;
    }

    private static int parseInt(String s) {
        try {
            return Integer.parseInt(s.trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
