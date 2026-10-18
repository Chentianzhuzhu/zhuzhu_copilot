import shutil

P = '/www/auth-server/app/config.py'
shutil.copy(P, P + '.bak.redisuser')

src = open(P, encoding='utf-8').read()

old1 = '    redis_password: str = ""\n'
new1 = ('    redis_password: str = ""\n'
        '    # Redis 6+ ACL username; empty = default user (backward compatible).\n'
        '    redis_user: str = ""\n')

old2 = ('    def redis_url(self) -> str:\n'
        '        if self.redis_password:\n'
        '            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"\n'
        '        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"\n')

new2 = ('    def redis_url(self) -> str:\n'
        '        if self.redis_user and self.redis_password:\n'
        '            return f"redis://{self.redis_user}:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"\n'
        '        if self.redis_password:\n'
        '            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"\n'
        '        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"\n')

assert src.count(old1) == 1, 'anchor1 not unique: %d' % src.count(old1)
assert src.count(old2) == 1, 'anchor2 not unique: %d' % src.count(old2)
src = src.replace(old1, new1).replace(old2, new2)
open(P, 'w', encoding='utf-8').write(src)
print('PATCHED OK')
