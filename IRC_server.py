import socket
import threading
import time

# 改为IPv6的通配地址，监听所有IPv6接口
HOST = "::"  # IPv6的0.0.0.0等效地址
PORT = 6667

# 频道 -> set(nick)
channels = {}
# 用户 -> connection对象
clients = {}

# 简单的用户对象
class User:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr  # 对于IPv6，addr是(IPv6地址, 端口, 流标签, 范围ID)
        self.nick = None
        self.user = None
        self.realname = None
        self.buffer = b""
        self.registered = False

    def send(self, data):
        if self.sock:
            try:
                self.sock.sendall((data + "\r\n").encode("utf-8"))
                print(f">> {data}")
            except Exception:
                self.close()

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def is_registered(self):
        return self.nick is not None and self.user is not None

# 专门处理一个用户的所有操作（每个用户都会有一个线程）
def handle_client(user: User):
    sock = user.sock
    try:
        while True:
            # 接收用户发来的消息
            data = sock.recv(4096)
            if not data:    # 如果没收到，说明用户连接断了
                break
            user.buffer += data # 消息存到缓冲器
            while b"\r\n" in user.buffer:
                # 从缓冲区提出一条消息
                line, user.buffer = user.buffer.split(b"\r\n", 1)
                line = line.decode("utf-8", errors="ignore").strip()
                if line:
                    print(f"<< {line}")
                    # 检查用户是否已注册（除了NICK和USER命令）
                    if not user.registered and not line.upper().startswith(("NICK", "USER")):
                        user.send(":server 451 :You have not registered")
                        continue
                    process_command(user, line) # 处理消息
    except Exception as e:
        print(f"Error with client {user.addr}: {e}")
    finally:
        # 清理用户
        leave_all_channels(user)
        if user.nick and user.nick in clients:
            del clients[user.nick]
        user.close()
        print(f"Connection closed: {user.addr}")

def process_command(user: User, line: str):
    parts = line.split()
    if not parts:
        return
    cmd = parts[0].upper()

    if cmd == "NICK":
        if len(parts) >= 2:
            old_nick = user.nick
            new_nick = parts[1]

            if old_nick == new_nick:
                return
            
            if new_nick in clients:
                user.send(f":server 433 {old_nick or '*'} {new_nick} :Nickname is already in use")
                return
            
            # 更新昵称
            if old_nick and old_nick in clients:
                del clients[old_nick]
            
            user.nick = new_nick
            clients[new_nick] = user
            
            # 检查是否完成注册
            if user.user and not user.registered:
                user.registered = True
                send_welcome_messages(user)
            
            # 通知所有频道成员昵称变更
            for channel_name, members in channels.items():
                if user in members:
                    for member in members:
                        if member != user:  # 不发送给自己
                            member.send(f":{old_nick or '*'}!{user.user or '*'}@{user.addr[0]} NICK :{new_nick}")
            
            user.send(f":{old_nick or '*'}!{user.user or '*'}@{user.addr[0]} NICK :{new_nick}")

    elif cmd == "USER":
        if len(parts) >= 5:
            user.user = parts[1]
            user.realname = line.split(":", 1)[1] if ":" in line else user.user
            
            # 检查是否完成注册
            if user.nick and not user.registered:
                user.registered = True
                send_welcome_messages(user)

    elif cmd == "JOIN":
        if len(parts) >= 2 and user.nick:
            channel_list = parts[1].split(",")
            for ch in channel_list:
                if not ch.startswith("#"):
                    ch = "#" + ch
                    
                if ch not in channels:
                    channels[ch] = set()
                
                # 检查用户是否已经在频道中
                if user not in channels[ch]:
                    channels[ch].add(user)
                    
                    # 通知所有频道成员（包括自己）
                    for member in channels[ch]:
                        member.send(f":{user.nick}!{user.user}@{user.addr[0]} JOIN {ch}")
                    
                    # 发送频道信息
                    user.send(f":server 331 {user.nick} {ch} :No topic is set")
                    names = " ".join([u.nick for u in channels[ch]])
                    user.send(f":server 353 {user.nick} = {ch} :{names}")
                    user.send(f":server 366 {user.nick} {ch} :End of /NAMES list")
                else:
                    user.send(f":server 443 {user.nick} {ch} :You are already in that channel")

    elif cmd == "MODE":
        if len(parts) >= 2:
            target = parts[1]
            if target.startswith("#") and target in channels:
                user.send(f":server 324 {user.nick} {target} +nt")
            else:
                user.send(f":server 501 {user.nick} :Unknown MODE command")
        else:
            user.send(f":server 461 {user.nick} MODE :Not enough parameters")

    elif cmd == "WHO":
        if len(parts) >= 2:
            target = parts[1]
            if target.startswith("#") and target in channels:
                for member in channels[target]:
                    user.send(f":server 352 {user.nick} {target} {member.user} {member.addr[0]} server {member.nick} H :0 {member.realname}")
                user.send(f":server 315 {user.nick} {target} :End of /WHO list")
            else:
                user.send(f":server 403 {user.nick} {target} :No such channel")
        else:
            user.send(f":server 461 {user.nick} WHO :Not enough parameters")
            
    elif cmd == "PRIVMSG":
        if len(parts) >= 3:
            target = parts[1]
            message = line.split(":", 1)[1] if ":" in line else ""
            if target.startswith("#"):
                if target in channels:
                    if user in channels[target]:
                        for member in channels[target]:
                            if member != user:
                                member.send(f":{user.nick}!{user.user}@{user.addr[0]} PRIVMSG {target} :{message}")
                    else:
                        user.send(f":server 404 {user.nick} {target} :Cannot send to channel")
                else:
                    user.send(f":server 403 {user.nick} {target} :No such channel")
            else:
                # 私聊功能
                if target in clients:
                    clients[target].send(f":{user.nick}!{user.user}@{user.addr[0]} PRIVMSG {target} :{message}")
                else:
                    user.send(f":server 401 {user.nick} {target} :No such nick/channel")
                    
    elif cmd == "PART":
        if len(parts) >= 2:
            channel_name = parts[1]
            reason = line.split(":", 1)[1] if ":" in line else "Leaving"
            if channel_name in channels and user in channels[channel_name]:
                channels[channel_name].remove(user)
                # 通知频道成员
                for member in channels[channel_name]:
                    member.send(f":{user.nick}!{user.user}@{user.addr[0]} PART {channel_name} :{reason}")
                # 如果是空频道，删除它
                if not channels[channel_name]:
                    del channels[channel_name]
            else:
                user.send(f":server 442 {user.nick} {channel_name} :You're not on that channel")
                
    elif cmd == "PING":
        token = parts[1] if len(parts) >= 2 else ""
        user.send(f":server PONG server :{token}")
        
    elif cmd == "QUIT":
        reason = line.split(":", 1)[1] if ":" in line else "Client quit"
        # 通知所有同频道用户
        for ch in list(channels.keys()):
            if user in channels[ch]:
                for member in list(channels[ch]):
                    if member != user:
                        member.send(f":{user.nick}!{user.user}@{user.addr[0]} QUIT :Quit: {reason}")
        user.close()
        
    elif cmd == "LIST":
        user.send(f":server 321 {user.nick} Channel :Users Name")
        for channel, members in channels.items():
            user.send(f":server 322 {user.nick} {channel} {len(members)} :")
        user.send(f":server 323 {user.nick} :End of /LIST")
        
    else:
        user.send(f":server 421 {user.nick} {cmd} :Unknown command")

def send_welcome_messages(user: User):
    """发送欢迎消息"""
    user.send(f":server 001 {user.nick} :Welcome to the our group's IRC {user.nick}")
    user.send(f":server 002 {user.nick} :Your host is {HOST}, running version 1.0")
    user.send(f":server 003 {user.nick} :This server was created for testing")
    user.send(f":server 004 {user.nick} :server 1.0 o o")
    user.send(f":server 375 {user.nick} :- server Message of the Day -")
    user.send(f":server 372 {user.nick} :- Welcome to our IRC server!")
    user.send(f":server 376 {user.nick} :End of /MOTD command.")

# 用户离开时，从所有频道删除
def leave_all_channels(user: User):
    """用户离开所有频道"""
    channels_to_remove = []
    for ch, members in list(channels.items()):
        if user in members:
            members.remove(user)
            # 通知频道其他成员
            for member in members:
                member.send(f":{user.nick}!{user.user}@{user.addr[0]} PART {ch} :Connection closed")
            if not members:
                channels_to_remove.append(ch)
    
    # 删除空频道
    for ch in channels_to_remove:
        del channels[ch]

def start_server():
    # 创建IPv6 TCP socket，使用AF_INET6协议族
    server_sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    # 设置端口复用
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 绑定到IPv6地址和端口
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    print(f"IRC-like server listening on [{HOST}]:{PORT} (IPv6)")

    try:
        while True:
            client_sock, addr = server_sock.accept()
            user = User(client_sock, addr)
            print(f"New connection from {addr}")
            t = threading.Thread(target=handle_client, args=(user,), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("Server shutting down.")
    finally:
        server_sock.close()

if __name__ == "__main__":
    start_server()