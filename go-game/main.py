"""
围棋游戏主界面 - pygame实现的双人围棋对弈程序
"""

import pygame
import sys
from go_logic import GoGame
from game_logger import GameLogger
from datetime import datetime

# 初始化pygame
pygame.init()

# 常量定义
BOARD_SIZE = 19
CELL_SIZE = 35
MARGIN = 50
WINDOW_WIDTH = MARGIN * 2 + CELL_SIZE * (BOARD_SIZE - 1)
WINDOW_HEIGHT = WINDOW_WIDTH + 120

# 颜色定义
COLOR_BOARD = '#DEB887'
COLOR_BLACK = '#000000'
COLOR_WHITE = '#FFFFFF'
COLOR_BORDER = '#8B4513'
COLOR_TEXT = '#333333'
COLOR_HIGHLIGHT = '#FFD700'
COLOR_LAST_MOVE = '#FF6B6B'


class GoGameApp:
    def __init__(self):
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption('双人围棋对弈')
        
        self.game = GoGame(BOARD_SIZE)
        self.logger = GameLogger('logs')
        
        self.font = pygame.font.SysFont('microsoftyahei', 16)
        self.font_large = pygame.font.SysFont('microsoftyahei', 24, bold=True)
        self.font_small = pygame.font.SysFont('microsoftyahei', 14)
        
        self.player_names = {'黑方': '玩家A', '白方': '玩家B'}
        self.show_log = False
        self.log_content = ''
        self.show_score = False
        
        self.last_move = None
        self.buttons = {}
        
        self.start_new_game()
    
    def start_new_game(self):
        """开始新对局"""
        self.game.reset()
        self.game.time_black = 60 * 30
        self.game.time_white = 60 * 30
        self.logger.start_new_game(self.player_names['黑方'], self.player_names['白方'])
        self.last_move = None
        self.time_last_switch = datetime.now()
    
    def draw_board(self):
        """绘制棋盘"""
        # 棋盘背景
        pygame.draw.rect(self.screen, COLOR_BOARD, 
                        (MARGIN, MARGIN, CELL_SIZE * (BOARD_SIZE - 1), CELL_SIZE * (BOARD_SIZE - 1)))
        
        # 绘制网格线
        for i in range(BOARD_SIZE):
            x = MARGIN + i * CELL_SIZE
            y = MARGIN + i * CELL_SIZE
            
            pygame.draw.line(self.screen, COLOR_BORDER, (MARGIN, y), 
                           (MARGIN + (BOARD_SIZE - 1) * CELL_SIZE, y), 1)
            pygame.draw.line(self.screen, COLOR_BORDER, (x, MARGIN), 
                           (x, MARGIN + (BOARD_SIZE - 1) * CELL_SIZE), 1)
        
        # 绘制星位
        star_points = [(3, 3), (3, 9), (3, 15), (9, 3), (9, 9), (9, 15), 
                      (15, 3), (15, 9), (15, 15)]
        for sx, sy in star_points:
            pygame.draw.circle(self.screen, COLOR_BORDER, 
                            (MARGIN + sx * CELL_SIZE, MARGIN + sy * CELL_SIZE), 4)
        
        # 绘制棋子
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if self.game.board[y][x] != 0:
                    color = COLOR_BLACK if self.game.board[y][x] == 1 else COLOR_WHITE
                    pos = (MARGIN + x * CELL_SIZE, MARGIN + y * CELL_SIZE)
                    
                    if self.last_move and (x, y) == self.last_move:
                        pygame.draw.circle(self.screen, COLOR_LAST_MOVE, pos, int(CELL_SIZE * 0.48), 3)
                    
                    pygame.draw.circle(self.screen, color, pos, int(CELL_SIZE * 0.45))
                    
                    if self.game.board[y][x] == 1:
                        pygame.draw.circle(self.screen, '#333333', pos, int(CELL_SIZE * 0.45), 1)
                    else:
                        pygame.draw.circle(self.screen, '#CCCCCC', pos, int(CELL_SIZE * 0.45), 1)
    
    def draw_ui(self):
        """绘制UI界面"""
        # 标题
        title = self.font_large.render('双人围棋对弈', True, COLOR_TEXT)
        self.screen.blit(title, (WINDOW_WIDTH // 2 - title.get_width() // 2, 10))
        
        # 玩家信息
        self.draw_player_info()
        
        # 按钮
        self.draw_buttons()
        
        # 面板
        if self.show_log:
            self.draw_log_panel()
        if self.show_score:
            self.draw_score_panel()
    
    def draw_player_info(self):
        """绘制玩家信息"""
        y_pos = MARGIN + CELL_SIZE * (BOARD_SIZE - 1) + 20
        
        black_text = "黑方: {} | 剩余: {} | 提子: {}".format(
            self.player_names['黑方'], 
            self.format_time(self.game.time_black),
            self.game.black_stones_captured
        )
        black_surf = self.font.render(black_text, True, COLOR_BLACK)
        self.screen.blit(black_surf, (MARGIN, y_pos))
        
        white_text = "白方: {} | 剩余: {} | 提子: {}".format(
            self.player_names['白方'],
            self.format_time(self.game.time_white),
            self.game.white_stones_captured
        )
        pygame.draw.rect(self.screen, '#555555', (MARGIN + 300, y_pos - 5, 400, 25))
        white_surf = self.font.render(white_text, True, COLOR_WHITE)
        self.screen.blit(white_surf, (MARGIN + 300, y_pos))
        
        current_text = "当前: {}".format('黑方' if self.game.current_player == 1 else '白方')
        current_surf = self.font_large.render(current_text, True, COLOR_TEXT)
        self.screen.blit(current_surf, (WINDOW_WIDTH - current_surf.get_width() - 20, y_pos))
    
    def draw_buttons(self):
        """绘制控制按钮"""
        y_pos = MARGIN + CELL_SIZE * (BOARD_SIZE - 1) + 60
        
        buttons = [
            ('悔棋', 0),
            ('虚手', 100),
            ('局势', 200),
            ('日志', 300),
            ('新局', 400),
            ('认输', 500)
        ]
        
        for text, x_offset in buttons:
            btn_rect = pygame.Rect(MARGIN + x_offset, y_pos, 80, 30)
            pygame.draw.rect(self.screen, '#4A90D9', btn_rect, border_radius=5)
            btn_text = self.font.render(text, True, COLOR_WHITE)
            self.screen.blit(btn_text, (btn_rect.x + btn_rect.width // 2 - btn_text.get_width() // 2,
                                      btn_rect.y + btn_rect.height // 2 - btn_text.get_height() // 2))
            self.buttons[text] = btn_rect
    
    def draw_log_panel(self):
        """绘制日志面板"""
        panel_x = WINDOW_WIDTH - 280
        panel_y = MARGIN
        panel_width = 260
        panel_height = CELL_SIZE * (BOARD_SIZE - 1)
        
        pygame.draw.rect(self.screen, '#F5F5F5', (panel_x, panel_y, panel_width, panel_height))
        pygame.draw.rect(self.screen, COLOR_BORDER, (panel_x, panel_y, panel_width, panel_height), 2)
        
        title = self.font_large.render('对局日志', True, COLOR_TEXT)
        self.screen.blit(title, (panel_x + 10, panel_y + 10))
        
        if self.log_content:
            lines = self.log_content.split('\n')
            for i, line in enumerate(lines[:20]):
                text_surf = self.font_small.render(line, True, COLOR_TEXT)
                self.screen.blit(text_surf, (panel_x + 10, panel_y + 40 + i * 18))
    
    def draw_score_panel(self):
        """绘制局势面板"""
        panel_x = WINDOW_WIDTH - 280
        panel_y = MARGIN
        panel_width = 260
        panel_height = CELL_SIZE * (BOARD_SIZE - 1)
        
        pygame.draw.rect(self.screen, '#F5F5F5', (panel_x, panel_y, panel_width, panel_height))
        pygame.draw.rect(self.screen, COLOR_BORDER, (panel_x, panel_y, panel_width, panel_height), 2)
        
        title = self.font_large.render('当前局势', True, COLOR_TEXT)
        self.screen.blit(title, (panel_x + 10, panel_y + 10))
        
        status = self.game.get_game_status()
        
        lines = [
            "黑方棋子: {}".format(status['black_stones']),
            "白方棋子: {}".format(status['white_stones']),
            "黑方提子: {}".format(status['black_captured']),
            "白方提子: {}".format(status['white_captured']),
            "总手数: {}".format(status['move_count']),
            "连续虚手: {}".format(status['pass_count']),
            "",
            "提示: 双方连续虚手结束对局"
        ]
        
        for i, line in enumerate(lines):
            text_surf = self.font_small.render(line, True, COLOR_TEXT)
            self.screen.blit(text_surf, (panel_x + 10, panel_y + 40 + i * 20))
    
    def format_time(self, seconds):
        """格式化时间显示"""
        if seconds <= 0:
            return '超时!'
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return '{:02d}:{:02d}'.format(mins, secs)
    
    def handle_click(self, pos):
        """处理鼠标点击"""
        x, y = pos
        
        if MARGIN <= x <= MARGIN + (BOARD_SIZE - 1) * CELL_SIZE and \
           MARGIN <= y <= MARGIN + (BOARD_SIZE - 1) * CELL_SIZE:
            
            board_x = round((x - MARGIN) / CELL_SIZE)
            board_y = round((y - MARGIN) / CELL_SIZE)
            
            if 0 <= board_x < BOARD_SIZE and 0 <= board_y < BOARD_SIZE:
                result = self.game.place_stone(board_x, board_y)
                if result['success']:
                    self.last_move = (board_x, board_y)
                    self.logger.add_move({
                        'move': len(self.game.history) + 1,
                        'player': 3 - self.game.current_player,
                        'coord': self.game.to_board_coord(board_x, board_y),
                        'time': datetime.now().strftime('%H:%M:%S'),
                        'captured': result.get('captured', []),
                        'timestamp': datetime.now().isoformat()
                    })
                    self.time_last_switch = datetime.now()
                    return True
        
        return False
    
    def handle_button_click(self, pos):
        """处理按钮点击"""
        x, y = pos
        
        for text, rect in self.buttons.items():
            if rect.collidepoint(x, y):
                if text == '悔棋':
                    self.handle_undo()
                elif text == '虚手':
                    self.handle_pass()
                elif text == '局势':
                    self.show_score = not self.show_score
                    self.show_log = False
                elif text == '日志':
                    self.show_log = not self.show_log
                    self.show_score = False
                    if self.show_log:
                        self.log_content = self.logger.export_to_text()
                elif text == '新局':
                    self.start_new_game()
                elif text == '认输':
                    self.handle_resign()
                return True
        return False
    
    def handle_undo(self):
        """悔棋"""
        if self.game.history:
            last_move = self.game.history.pop()
            coord = last_move['coord']
            if coord != 'PASS':
                x, y = self.game.from_board_coord(coord)
                if x is not None:
                    self.game.board[y][x] = 0
                    self.game.current_player = last_move['player']
                    self.last_move = None
    
    def handle_pass(self):
        """虚手"""
        self.game.pass_move()
        self.logger.add_move({
            'move': len(self.game.history),
            'player': 3 - self.game.current_player,
            'coord': 'PASS',
            'time': datetime.now().strftime('%H:%M:%S'),
            'captured': [],
            'timestamp': datetime.now().isoformat()
        })
    
    def handle_resign(self):
        """认输"""
        winner = 3 - self.game.current_player
        result = self.game.calculate_score_chinese()
        result['winner'] = winner
        result['method'] = 'resign'
        self.logger.end_game(result)
        
        winner_name = self.player_names['黑方'] if winner == 1 else self.player_names['白方']
        self.show_message('{} 获胜！对方认输'.format(winner_name))
    
    def show_message(self, msg):
        """显示消息"""
        text_surf = self.font_large.render(msg, True, COLOR_TEXT)
        rect = text_surf.get_rect(center=(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2))
        self.screen.blit(text_surf, rect)
        pygame.display.update()
    
    def update_timers(self):
        """更新计时器"""
        now = datetime.now()
        if hasattr(self, 'time_last_switch'):
            elapsed = (now - self.time_last_switch).total_seconds()
            if self.game.current_player == 1:
                self.game.time_black -= elapsed
            else:
                self.game.time_white -= elapsed
            self.time_last_switch = now
    
    def run(self):
        """主循环"""
        clock = pygame.time.Clock()
        self.time_last_switch = datetime.now()
        
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        pos = event.pos
                        self.handle_click(pos)
                        self.handle_button_click(pos)
            
            self.screen.fill('#F0E6D2')
            self.draw_board()
            self.draw_ui()
            self.update_timers()
            
            pygame.display.flip()
            clock.tick(30)


if __name__ == '__main__':
    app = GoGameApp()
    app.run()
