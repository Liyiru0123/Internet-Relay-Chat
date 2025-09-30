import socket
import threading
import time

# Listen on all IPv6 interfaces
HOST = "::"
PORT = 6667

# Global dictionaries to store channels and clients
channels = {}  # Format: {channel_name: set_of_users}
clients = {}   # Format: {nickname: user_object}

# Irresponsive time out (s)
IDLE_TIMEOUT = 60

# Simple User class to represent an IRC client
class User:
    def __init__(self, sock, addr):
        self.sock = sock          # Socket object for connection
        self.addr = addr          # Client address (IPv6, port, flow label, scope ID)
        self.nick = None          # Nickname
        self.user = None          # Username
        self.realname = None      # Real name （set by USER command）
        self.buffer = b""         # Buffer for incomplete messages
        self.registered = False   # Registration status (True after NICK+USER)
        self.is_quit = False      # Flag to indicate user wants to quit

    # send data in required format
    def send(self, data):
        if self.sock:
            try:
                self.sock.sendall((data + "\r\n").encode("utf-8"))
                print(f"{self.addr} <- {data}")  # log message
            except Exception:
                # Close connection on error
                self.close()

    # close socket
    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


# main func to start IRC server
def start_server():
    # Create IPv6 TCP socket
    server_sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    # Allow port reuse to avoid "address already in use" errors
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Bind to all IPv6 interfaces on specified port
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)  # Queue up to 5 connections
    print(f"IRC server listening on [{HOST}]:{PORT} (IPv6)")

    try:
        while True:
            # Accept incoming connections
            client_sock, addr = server_sock.accept()
            # Create User object for the new connection
            user = User(client_sock, addr)
            print(f"New connection from {addr}")
            # Start a new thread to handle this client
            t = threading.Thread(target=handle_client, args=(user,), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("Server shutting down.")
    finally:
        server_sock.close()

# handle clients run in separate thread
def handle_client(user: User):
    sock = user.sock
    try:
        sock.settimeout(IDLE_TIMEOUT)
        while not user.is_quit:
            try:
                # Receive data from client
                data = sock.recv(4096)
            except socket.timeout:
                # irresponsive time out
                user.send(f":server NOTICE {user.nick or 'unknown'} :Detect you are inresponsive, disconnecting...")
                print(f"Detected user {user.nick or user.addr} is idle, disconnecting...")
                break

            if not data:  # Empty data means client disconnected
                break
            # Append to buffer (messages might be split across packets)
            user.buffer += data
            # Process complete messages (separated by CRLF)
            while b"\r\n" in user.buffer:
                # Extract one complete message
                line, user.buffer = user.buffer.split(b"\r\n", 1)
                line = line.decode("utf-8", errors="ignore").strip()
                if line:
                    print(f"{user.addr} -> {line}")  # Log incoming message
                    # Check if user is registered (except for registration commands)
                    if not user.registered and not line.upper().startswith(("CAP", "NICK", "USER")):
                        user.send(":server 451 :You have not registered")
                        continue
                    # Process the IRC command
                    process_command(user, line)
    except Exception as e:
        # ignore connection closed error
        if "10053" not in str(e) and not isinstance(e, ConnectionResetError):
            print(f"Error with {user.addr}: {e}")
    finally:
        # Cleanup when client disconnects
        leave_all_channels(user)  # Remove from all channels
        if user.nick and user.nick in clients:
            del clients[user.nick]  # Remove from clients list
        user.close()  # Close socket
        print(f"Disconnected connection from: {user.addr} (leaving)")

# welcome message when first registration
def send_welcome_messages(user: User):
    user.send(f":server 001 {user.nick} :Welcome to the our group's IRC {user.nick}")
    user.send(f":server 002 {user.nick} :Running version 2.1")
    user.send(f":server 003 {user.nick} :This server was created for assignment1")
    user.send(f":server 004 {user.nick} :server 2.1")

# remove user from all channels and broadcast
def leave_all_channels(user: User):
    channels_to_remove = []
    for ch, members in list(channels.items()):
        if user in members:
            members.remove(user)
            # Notify other channel members about the departure
            for member in list(members):
                member.send(f":{user.nick}!{user.user}@{user.addr[0]} PART {ch} :Connection closed")
            # Mark empty channels for deletion
            if not members:
                channels_to_remove.append(ch)
    
    # Clean up empty channels
    for ch in channels_to_remove:
        del channels[ch]

#Process the IRC commands
def process_command(user: User, line: str):
    parts = line.split()
    if not parts:
        return
    cmd = parts[0].upper()  # Extract command

    # change nickname
    if cmd == "NICK":
        if len(parts) >= 2:
            old_nick = user.nick
            new_nick = parts[1]

            # Ignore if nickname isn't changing
            if old_nick == new_nick:
                return
            
            # Check if nickname is already in use
            if new_nick in clients:
                user.send(f":server 433 {old_nick or '*'} {new_nick} :Nickname is already in use")
                return
            
            # Update nickname in global clients dictionary
            if old_nick and old_nick in clients:
                del clients[old_nick]
            
            user.nick = new_nick
            clients[new_nick] = user
            
            # Complete registration if this was the last step
            if user.user and not user.registered:
                user.registered = True
                send_welcome_messages(user)
            
            # Broadcast to all channel members about nickname change
            for channel_name, members in channels.items():
                if user in members:
                    for member in members:
                        if member != user:  # Don't send to self
                            member.send(f":{old_nick or '*'}!{user.user or '*'}@{user.addr[0]} NICK :{new_nick}")
            
            # Confirm nickname change to user
            user.send(f":{old_nick or '*'}!{user.user or '*'}@{user.addr[0]} NICK :{new_nick}")
    
    # set username and realname
    elif cmd == "USER":
        if len(parts) >= 4:
            user.user = parts[1]
        
            # process realname
            if ":" in line:
                user.realname = line.split(":", 1)[1]   # If there is ':' take all the subsequent content
            else:
                user.realname = " ".join(parts[4:]) if len(parts) >=5 else user.user    # if not, concatenate parts[4:] (compatible with short input.）
                
            # Complete registration if this was the last step
            if user.nick and not user.registered:
                user.registered = True
                send_welcome_messages(user)
        else:
            user.send(f":server 461 {user.nick or '*'} USER :Not enough parameters")

    # join a channel
    elif cmd == "JOIN":
        if len(parts) >= 2 and user.nick:
            channel_list = parts[1].split(",")  # Support multiple channels
            for ch in channel_list:
                # Validate channel name (must start with #)
                if not ch.startswith("#"):
                    user.send(f":server 403 {user.nick} {ch} :No such channel")
                    continue  # Skip invalid channel
                    
                # Create channel if it doesn't exist
                if ch not in channels:
                    channels[ch] = set()
                
                # Check if user is already in channel
                if user not in channels[ch]:
                    channels[ch].add(user)
                    
                    # Notify all channel members about the join
                    for member in channels[ch]:
                        member.send(f":{user.nick}!{user.user}@{user.addr[0]} JOIN {ch}")
                    
                    # Send channel information to joining user
                    user.send(f":server 331 {user.nick} {ch} :No topic is set")
                    # Send list of nicknames in channel
                    names = " ".join([u.nick for u in channels[ch]])
                    user.send(f":server 353 {user.nick} = {ch} :{names}")
                    user.send(f":server 366 {user.nick} {ch} :End of /NAMES list")
                else:
                    user.send(f":server 443 {user.nick} {ch} :You are already in that channel")
    
    # leave the channel
    elif cmd == "PART":
        if len(parts) >= 2:
            ch = parts[1]
            reason = line.split(":", 1)[1] if ":" in line else ""
            if not ch.startswith("#") or ch not in channels:
                user.send(f":server 403 {user.nick} {ch} :No such channel")
                return
            
            if user not in channels[ch]:
                user.send(f":server 442 {user.nick} {ch} :You're not on that channel")
                return
        
            # leave the channel
            user.send(f":{user.nick}!{user.user}@{user.addr[0]} PART {ch} :{reason}")
            channels[ch].remove(user)

            # broadcast to others in that channel
            for member in channels[ch]:
                member.send(f":{user.nick}!{user.user}@{user.addr[0]} PART {ch} :{reason}")
            
            # remove the channel if nobody else
            if not channels[ch]:
                del channels[ch]

    # list users in a channel
    elif cmd == "WHO":
        if len(parts) >= 2:
            target = parts[1]
            if target.startswith("#") and target in channels:
                # Send information about each channel member
                for member in channels[target]:
                    user.send(f":server 352 {user.nick} {target} {member.user} {member.addr[0]} server {member.nick} H :0 {member.realname}")
                user.send(f":server 315 {user.nick} {target} :End of /WHO list")
            else:
                user.send(f":server 403 {user.nick} {target} :No such channel")
        else:
            user.send(f":server 461 {user.nick} WHO :Not enough parameters")

    # handle PRIVMSG command for channel and private messages        
    elif cmd == "PRIVMSG":
        if len(parts) >= 3:
            target = parts[1]
            message = line.split(":", 1)[1] if ":" in line else ""
            
            if target.startswith("#"):  # Channel message
                if target in channels:
                    if user in channels[target]:  # Check if user is in channel
                        # Relay message to all other channel members
                        for member in channels[target]:
                            if member != user:
                                member.send(f":{user.nick}!{user.user}@{user.addr[0]} PRIVMSG {target} :{message}")
                    else:
                        user.send(f":server 404 {user.nick} {target} :Cannot send to channel")
                else:
                    user.send(f":server 403 {user.nick} {target} :No such channel")
            else:  # Private message
                if target in clients:
                    clients[target].send(f":{user.nick}!{user.user}@{user.addr[0]} PRIVMSG {target} :{message}")
                else:
                    user.send(f":server 401 {user.nick} {target} :No such nick/channel")
                   
    elif cmd == "PING":
        token = parts[1] if len(parts) >= 2 else ""
        user.send(f":server PONG server :{token}")

    # quit the server    
    elif cmd == "QUIT":
        reason = line.split(":", 1)[1] if ":" in line else "Client quit"
        # broadcast to all channel members
        for ch in list(channels.keys()):
            if user in channels[ch]:
                for member in list(channels[ch]):
                    if member != user: # the quit user no need broadcast
                        member.send(f":{user.nick}!{user.user}@{user.addr[0]} QUIT :Quit: {reason}")
        # user.close()
        user.is_quit = True

    # list all channels    
    elif cmd == "LIST":
        user.send(f":server 321 {user.nick} Channel :Users Name")
        for channel, members in channels.items():
            user.send(f":server 322 {user.nick} {channel} {len(members)} :")
        user.send(f":server 323 {user.nick} :End of /LIST")

    # handle unknown commands    
    else:
        user.send(f":server 421 {user.nick} {cmd} :Unknown command")


if __name__ == "__main__":
    start_server()