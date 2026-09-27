"""
对局日志系统
支持时间戳记录、文件保存、历史对局查看
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional


class GameLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.current_log = None
        self.log_file_path = None
    
    def start_new_game(self, black_name: str, white_name: str) -> str:
        """开始新对局，创建日志文件"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file_path = os.path.join(self.log_dir, f"game_{timestamp}.json")
        
        game_info = {
            'game_id': timestamp,
            'start_time': datetime.now().isoformat(),
            'black_player': black_name,
            'white_player': white_name,
            'moves': [],
            'status': 'in_progress',
            'result': None
        }
        
        self._save_game_info(game_info)
        return self.log_file_path
    
    def add_move(self, move_record: Dict):
        """添加一手棋记录"""
        if self.log_file_path is None:
            return
        
        game_info = self._load_game_info()
        if game_info and game_info['status'] == 'in_progress':
            game_info['moves'].append(move_record)
            self._save_game_info(game_info)
    
    def end_game(self, result: Dict):
        """结束对局"""
        if self.log_file_path is None:
            return
        
        game_info = self._load_game_info()
        if game_info:
            game_info['status'] = 'finished'
            game_info['end_time'] = datetime.now().isoformat()
            game_info['result'] = result
            self._save_game_info(game_info)
    
    def _save_game_info(self, game_info: Dict):
        """保存游戏信息到文件"""
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            json.dump(game_info, f, ensure_ascii=False, indent=2)
    
    def _load_game_info(self) -> Optional[Dict]:
        """加载游戏信息"""
        if self.log_file_path and os.path.exists(self.log_file_path):
            with open(self.log_file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def get_last_game(self) -> Optional[Dict]:
        """获取最后一局对局"""
        if not os.path.exists(self.log_dir):
            return None
        
        files = sorted([f for f in os.listdir(self.log_dir) if f.startswith('game_')])
        if not files:
            return None
        
        last_file = os.path.join(self.log_dir, files[-1])
        with open(last_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_all_games(self, limit: int = 10) -> List[Dict]:
        """获取所有对局记录"""
        if not os.path.exists(self.log_dir):
            return []
        
        files = sorted([f for f in os.listdir(self.log_dir) if f.startswith('game_')])
        games = []
        
        for filename in files[-limit:]:
            filepath = os.path.join(self.log_dir, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                game = json.load(f)
                games.append(game)
        
        return games
    
    def export_to_text(self, game_id: Optional[str] = None) -> str:
        """导出为文本格式"""
        if game_id:
            files = [f for f in os.listdir(self.log_dir) if f == f"game_{game_id}.json"]
            if not files:
                return "未找到该对局"
            filepath = os.path.join(self.log_dir, files[0])
            with open(filepath, 'r', encoding='utf-8') as f:
                game = json.load(f)
        else:
            game = self.get_last_game()
            if not game:
                return "暂无对局记录"
        
        lines = []
        lines.append(f"对局ID: {game['game_id']}")
        lines.append(f"黑方: {game['black_player']}")
        lines.append(f"白方: {game['white_player']}")
        lines.append(f"开始时间: {game['start_time']}")
        lines.append(f"结束时间: {game.get('end_time', '进行中')}")
        lines.append("=" * 50)
        
        for move in game['moves']:
            player = "黑" if move['player'] == 1 else "白"
            coord = move.get('coord', 'PASS')
            captured = move.get('captured', [])
            captured_str = f" 提{len(captured)}子" if captured else ""
            lines.append(f"{move['move']:3d}. {player}方 {coord}{captured_str}  ({move['time']})")
        
        if game.get('result'):
            result = game['result']
            winner = "黑方" if result['winner'] == 1 else "白方"
            lines.append("=" * 50)
            lines.append(f"结果: {winner}胜 {result['margin']:.1f} 子")
        
        return "\n".join(lines)
    
    def list_games(self) -> List[str]:
        """列出所有对局"""
        if not os.path.exists(self.log_dir):
            return []
        
        files = sorted([f for f in os.listdir(self.log_dir) if f.startswith('game_')])
        return [f.replace('.json', '') for f in files]


if __name__ == '__main__':
    logger = GameLogger()
    print("对局日志系统初始化完成")
    print(f"日志目录: {logger.log_dir}")
