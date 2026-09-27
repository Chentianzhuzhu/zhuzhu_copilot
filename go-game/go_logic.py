"""
围棋核心逻辑模块
支持19x19棋盘、中国规则（数子法）、提子判断、劫争检测、胜负计算
"""

import copy
from datetime import datetime
from typing import List, Tuple, Optional, Dict


class GoGame:
    def __init__(self, board_size=19):
        self.board_size = board_size
        self.reset()
    
    def reset(self):
        """初始化棋盘状态"""
        self.board = [[0 for _ in range(self.board_size)] for _ in range(self.board_size)]
        self.current_player = 1  # 1=黑, 2=白
        self.pass_count = 0
        self.ko_position = None  # 劫争位置
        self.history = []  # 棋谱记录
        self.black_stones_captured = 0  # 黑方提子数
        self.white_stones_captured = 0  # 白方提子数
        self.time_black = 60 * 30  # 黑方时间(秒)，默认30分钟
        self.time_white = 60 * 30  # 白方时间(秒)，默认30分钟
    
    def to_board_coord(self, x: int, y: int) -> str:
        """坐标转换为棋盘表示 (如 A1, Q16)"""
        if x < 0 or x >= self.board_size or y < 0 or y >= self.board_size:
            return None
        col = chr(ord('A') + x) if x < 25 else ''
        row = str(self.board_size - y)
        return f"{col}{row}"
    
    def from_board_coord(self, coord: str) -> Optional[Tuple[int, int]]:
        """棋盘表示转换为坐标"""
        if len(coord) < 2:
            return None
        try:
            col = ord(coord[0].upper()) - ord('A')
            row = int(coord[1:])
            x = col
            y = self.board_size - row
            if 0 <= x < self.board_size and 0 <= y < self.board_size:
                return (x, y)
        except:
            pass
        return None
    
    def get_neighbors(self, x: int, y: int) -> List[Tuple[int, int]]:
        """获取相邻位置"""
        neighbors = []
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                neighbors.append((nx, ny))
        return neighbors
    
    def get_group(self, x: int, y: int) -> List[Tuple[int, int]]:
        """获取同色棋子组（连通块）"""
        color = self.board[y][x]
        if color == 0:
            return []
        group = []
        visited = set()
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            group.append((cx, cy))
            for nx, ny in self.get_neighbors(cx, cy):
                if (nx, ny) not in visited and self.board[ny][nx] == color:
                    stack.append((nx, ny))
        return group
    
    def get_liberties(self, x: int, y: int) -> int:
        """获取某位置的自由气数"""
        group = self.get_group(x, y)
        liberties = set()
        for gx, gy in group:
            for nx, ny in self.get_neighbors(gx, gy):
                if self.board[ny][nx] == 0:
                    liberties.add((nx, ny))
        return len(liberties)
    
    def remove_group(self, group: List[Tuple[int, int]]):
        """移除一组棋子"""
        for x, y in group:
            self.board[y][x] = 0
    
    def check_capture(self, x: int, y: int, color: int) -> List[Tuple[int, int]]:
        """检查落子后是否能提子，返回被提的敌方棋子列表"""
        opponent = 3 - color  # 对手颜色
        captured = []
        for nx, ny in self.get_neighbors(x, y):
            if self.board[ny][nx] == opponent and self.get_liberties(nx, ny) == 0:
                captured.extend(self.get_group(nx, ny))
        return captured
    
    def is_ko_valid(self, x: int, y: int, color: int) -> bool:
        """检查是否违反劫争规则"""
        if self.ko_position is None:
            return True
        # 简单劫：不能立刻回提
        return (x, y) != self.ko_position
    
    def place_stone(self, x: int, y: int) -> Dict:
        """落子，返回结果字典"""
        result = {
            'success': False,
            'message': '',
            'captured': [],
            'self_captured': [],
            'ko_position': None
        }
        
        # 检查位置是否已有子
        if self.board[y][x] != 0:
            result['message'] = f"({self.to_board_coord(x, y)}) 已有棋子"
            return result
        
        # 检查是否超时
        if self.current_player == 1 and self.time_black <= 0:
            result['message'] = "黑方超时判负"
            return result
        if self.current_player == 2 and self.time_white <= 0:
            result['message'] = "白方超时判负"
            return result
        
        color = self.current_player
        
        # 尝试落子
        self.board[y][x] = color
        
        # 检查提子
        captured = self.check_capture(x, y, color)
        result['captured'] = [(self.to_board_coord(cx, cy), self.board[cy][cx]) for cx, cy in captured]
        
        # 移除被提棋子
        if captured:
            if color == 1:
                self.white_stones_captured += len(captured)
            else:
                self.black_stones_captured += len(captured)
            self.remove_group(captured)
        
        # 检查自杀
        if self.get_liberties(x, y) == 0:
            # 自杀不允许（除非能提子）
            if not captured:
                self.board[y][x] = 0
                result['message'] = f"({self.to_board_coord(x, y)}) 自杀禁手"
                return result
            # 能提子的自杀是合法的（提子后有空位）
        
        # 检查劫争
        # 如果这手棋刚好形成劫（只提了一子，且该子只有一气），记录劫争位置
        if len(captured) == 1:
            cx, cy = captured[0]
            # 检查被提方此时是否只剩一口气
            opponent = 3 - color
            if self.get_liberties(cx, cy) == 0:
                result['ko_position'] = (cx, cy)
                self.ko_position = (cx, cy)
        
        # 记录历史
        move_record = {
            'move': len(self.history) + 1,
            'player': color,
            'coord': self.to_board_coord(x, y),
            'time': datetime.now().strftime('%H:%M:%S'),
            'captured': result['captured'],
            'timestamp': datetime.now().isoformat()
        }
        self.history.append(move_record)
        
        # 切换玩家
        self.current_player = 3 - color
        self.pass_count = 0
        
        result['success'] = True
        result['message'] = f"{color}方落子于 {self.to_board_coord(x, y)}"
        return result
    
    def pass_move(self) -> Dict:
        """虚手（pass）"""
        result = {'success': True, 'message': ''}
        
        self.pass_count += 1
        color = self.current_player
        self.current_player = 3 - color
        
        # 记录pass
        move_record = {
            'move': len(self.history) + 1,
            'player': 3 - self.current_player,  # 记录的是pass前的玩家
            'coord': 'PASS',
            'time': datetime.now().strftime('%H:%M:%S'),
            'captured': [],
            'timestamp': datetime.now().isoformat()
        }
        self.history.append(move_record)
        
        if self.pass_count >= 2:
            result['message'] = "双方连续虚手，对局结束"
        else:
            result['message'] = f"{'黑' if self.current_player == 2 else '白'}方虚手"
        
        return result
    
    def calculate_score_chinese(self) -> Dict:
        """中国规则数子法计算胜负"""
        # 需要双方确认终局
        # 这里实现简化版：计算双方领地 + 提子
        
        # 计算黑方和白方的领地（用洪水填充）
        black_area = self.flood_fill_area(1)
        white_area = self.flood_fill_area(2)
        
        # 计算双方棋子数
        black_stones = sum(row.count(1) for row in self.board)
        white_stones = sum(row.count(2) for row in self.board)
        
        # 中国规则：黑贴3又3/4子 = 7.5目
        # 黑方总得分 = 黑方领地 + 黑方棋子数
        # 黑方需超过 180.75 (361/2 + 3.75) 才能胜
        black_score = black_area[0] + black_stones
        white_score = white_area[0] + white_stones
        
        # 黑贴7.5目
        black_final = black_score
        white_final = white_score + 7.5
        
        if black_final > white_final:
            winner = 1
            margin = black_final - white_final
        else:
            winner = 2
            margin = white_final - black_final
        
        return {
            'black_area': black_area[0],
            'white_area': white_area[0],
            'black_stones': black_stones,
            'white_stones': white_stones,
            'black_score': black_final,
            'white_score': white_final,
            'winner': winner,
            'margin': margin
        }
    
    def flood_fill_area(self, color: int) -> Tuple[int, List[Tuple[int, int]]]:
        """计算某色领地（简化版：标记 Territory）"""
        visited = set()
        territory_count = 0
        territory_positions = []
        
        for y in range(self.board_size):
            for x in range(self.board_size):
                if (x, y) in visited or self.board[y][x] != 0:
                    continue
                
                # 洪水填充空位
                group = []
                stack = [(x, y)]
                while stack:
                    cx, cy = stack.pop()
                    if (cx, cy) in visited:
                        continue
                    visited.add((cx, cy))
                    group.append((cx, cy))
                    for nx, ny in self.get_neighbors(cx, cy):
                        if (nx, ny) not in visited and self.board[ny][nx] == 0:
                            stack.append((nx, ny))
                
                # 检查该区域边界是否有某色棋子
                black_border = False
                white_border = False
                for gx, gy in group:
                    for nx, ny in self.get_neighbors(gx, gy):
                        if self.board[ny][nx] == 1:
                            black_border = True
                        elif self.board[ny][nx] == 2:
                            white_border = True
                
                if black_border and not white_border:
                    territory_count += len(group)
                    territory_positions.extend(group)
        
        return (territory_count, territory_positions)
    
    def get_game_status(self) -> Dict:
        """获取当前局势"""
        black_stones = sum(row.count(1) for row in self.board)
        white_stones = sum(row.count(2) for row in self.board)
        
        return {
            'current_player': self.current_player,
            'black_stones': black_stones,
            'white_stones': white_stones,
            'black_captured': self.black_stones_captured,
            'white_captured': self.white_stones_captured,
            'pass_count': self.pass_count,
            'move_count': len(self.history),
            'time_black': self.time_black,
            'time_white': self.time_white
        }


if __name__ == '__main__':
    game = GoGame(19)
    print("围棋游戏初始化完成")
    print(f"棋盘大小: {game.board_size}x{game.board_size}")
    print(f"坐标示例: A1={game.to_board_coord(0, 18)}, Q16={game.to_board_coord(15, 3)}")
