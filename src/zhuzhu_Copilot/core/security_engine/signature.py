"""Authenticode 签名验证与签发者提取（crypt32 CryptQueryObject，纯 ctypes，真实 API）"""
import ctypes
import ctypes.wintypes as wt


def signer_subject(path: str) -> str:
    """提取第一个签名证书的简单显示名（CN）；无签名/失败返回空串"""
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        CERT_QUERY_OBJECT_FILE = 0x1
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
        CERT_QUERY_FORMAT_FLAG_ALL = 0xFFE
        X509_ASN_ENCODING = 0x1
        PKCS_7_ASN_ENCODING = 0x10000
        CERT_NAME_SIMPLE_DISPLAY_TYPE = 4

        crypt32.CryptQueryObject.argtypes = [wt.DWORD, wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
                                             ctypes.c_void_p]
        crypt32.CryptQueryObject.restype = wt.BOOL
        crypt32.CertFindCertificateInStore.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD,
                                                       ctypes.c_void_p, ctypes.c_void_p]
        crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p
        crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                               wt.LPWSTR, wt.DWORD]
        crypt32.CertGetNameStringW.restype = wt.DWORD
        crypt32.CertCloseStore.argtypes = [wt.HANDLE, wt.DWORD]

        h_store, h_msg = wt.HANDLE(), wt.HANDLE()
        ok = crypt32.CryptQueryObject(CERT_QUERY_OBJECT_FILE, path,
                                      CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
                                      CERT_QUERY_FORMAT_FLAG_ALL, 0,
                                      None, None, None, ctypes.byref(h_store),
                                      ctypes.byref(h_msg), None)
        if not ok or not h_store.value:
            return ""
        try:
            ctx = crypt32.CertFindCertificateInStore(
                h_store.value, X509_ASN_ENCODING | PKCS_7_ASN_ENCODING, 0, 0, None, None)
            if not ctx:
                return ""
            buf = ctypes.create_unicode_buffer(512)
            crypt32.CertGetNameStringW(ctx, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, buf, 512)
            return buf.value
        finally:
            crypt32.CertCloseStore(h_store.value, 0)
    except Exception:
        return ""