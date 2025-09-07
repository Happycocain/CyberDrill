# cyberdrill_pythonista_4_0.py
# CyberDrill 4.0 – stabilní prototyp s obtížnostmi, selftestem, exportem logů, novým UI a LAN multiplayerem
# iOS / Pythonista 3

import ui, time, random, collections, traceback, os, datetime, clipboard, socket, json

# ========== utils ==========
def err_str(e):
    try:
        return "".join(traceback.format_exception_only(type(e), e)).strip()
    except Exception:
        return str(e)

def clamp(x, lo, hi): return max(lo, min(hi, x))

def as_int(v, default):
    try: return int(v)
    except Exception: return default

def _scroll_tv_to_end(tv):
    try:
        if hasattr(tv, "content_size") and hasattr(tv, "content_offset"):
            y = max(0, tv.content_size[1] - tv.height)
            tv.content_offset = (0, y)
    except Exception as e:
        print("[ERR] scroll_to_end:", err_str(e))

def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# ========== obtížnosti ==========
class Difficulty:
    def __init__(self, name, time_mult=1.0, score_mult=1.0, penalty=5, ddos_mult=1.0, detect_sensitivity=1.0, hint_level=2):
        self.name = name
        self.time_mult = float(time_mult)
        self.score_mult = float(score_mult)
        self.penalty = int(penalty)
        self.ddos_mult = float(ddos_mult)
        self.detect_sensitivity = float(detect_sensitivity)
        self.hint_level = int(hint_level)

DIFFICULTIES = {
    "Easy":   Difficulty("Easy",   time_mult=1.3, score_mult=1.0, penalty=2,  ddos_mult=0.85, detect_sensitivity=1.2, hint_level=2),
    "Normal": Difficulty("Normal", time_mult=1.0, score_mult=1.0, penalty=5,  ddos_mult=1.0,  detect_sensitivity=1.0, hint_level=2),
    "Hard":   Difficulty("Hard",   time_mult=0.85,score_mult=1.2, penalty=8,  ddos_mult=1.2,  detect_sensitivity=0.9, hint_level=1),
    "Insane": Difficulty("Insane", time_mult=0.7, score_mult=1.5, penalty=12, ddos_mult=1.4,  detect_sensitivity=0.8, hint_level=1),
}
def get_diff(name): return DIFFICULTIES.get(name or "Normal", DIFFICULTIES["Normal"])

# ========== modely ==========
class MissionStep:
    def __init__(self, title, hint, validator, success_log, validator_fn=None):
        self.title = title
        self.hint = hint
        self.validator = validator
        self.success_log = success_log
        self.validator_fn = validator_fn

class Mission:
    def __init__(self, code, name, description, role, allowed, steps, time_limit):
        self.code = code; self.name = name; self.description = description
        self.role = role; self.allowed = allowed[:]; self.steps = steps[:]
        self.time_limit = int(time_limit)

# ========== NetPeer (LAN TCP, JSON lines, polling přes ui.delay) ==========
class NetPeer:
    def __init__(self, on_cmd, on_sync, logger):
        self.on_cmd = on_cmd; self.on_sync = on_sync; self.log = logger
        self.mode = None            # 'host' | 'client' | None
        self.code = ""; self.port = 0
        self._server = None         # host socket
        self._clients = []          # [(sock, addr)]
        self._sock = None           # client socket
        self._bufs = {}             # sock -> pending buffer
        self._polling = False

    def is_host(self): return self.mode == 'host'
    def is_client(self): return self.mode == 'client'

    def host(self, port=50555, code=""):
        try:
            self.leave()
            self.mode = 'host'; self.code = str(code or ""); self.port = int(port)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setblocking(False); s.bind(('', self.port)); s.listen(5)
            self._server = s; self._bufs = {}
            self._start_poll()
            return True, f"Host na portu {self.port} (code={self.code or 'none'})"
        except Exception as e:
            self.mode = None
            return False, "Host error: " + err_str(e)

    def join(self, host, port=50555, code=""):
        try:
            self.leave()
            self.mode = 'client'; self.code = str(code or ""); self.port = int(port)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0); s.connect((host, self.port))
            s.setblocking(False)
            self._sock = s; self._bufs = {s: b""}
            self._send(s, {"t":"hello","code":self.code})
            self._start_poll()
            return True, f"Připojeno k {host}:{self.port}"
        except Exception as e:
            self.mode = None
            return False, "Join error: " + err_str(e)

    def request_sync(self):
        if self.is_client() and self._sock:
            self._send(self._sock, {"t":"sync"})

    def send_cmd(self, text):
        if self.is_client() and self._sock:
            self._send(self._sock, {"t":"cmd","c":text,"code":self.code})

    def broadcast(self, payload):
        if not self.is_host(): return
        dead=[]
        for s,_ in list(self._clients):
            if not self._send(s, payload): dead.append(s)
        if dead:
            self._clients = [(x,a) for x,a in self._clients if x not in dead]

    def list_peers(self):
        if self.is_host(): return [f"{a[0]}:{a[1]}" for _,a in self._clients]
        if self.is_client():
            try: return [self._sock.getpeername()[0]]
            except: return []
        return []

    def leave(self):
        try:
            if self._server: 
                try: self._server.close()
                except: pass
            for s,_ in list(self._clients):
                try: s.close()
                except: pass
            if self._sock:
                try: self._sock.close()
                except: pass
        finally:
            self._server=None; self._clients=[]; self._sock=None; self._bufs={}; self.mode=None

    # interní
    def _start_poll(self):
        if self._polling: return
        self._polling=True; ui.delay(self._poll, 0.1)

    def _poll(self):
        self._polling=False
        try:
            if self.mode == 'host' and self._server:
                # accept
                try:
                    while True:
                        s, addr = self._server.accept()
                        s.setblocking(False); self._bufs[s]=b""; self._clients.append((s,addr))
                except BlockingIOError:
                    pass
                # recv
                for s,_ in list(self._clients):
                    self._recv_sock(s, host_side=True)
            elif self.mode == 'client' and self._sock:
                self._recv_sock(self._sock, host_side=False)
        except Exception as e:
            self.log("[NET] poll err: " + err_str(e))
        finally:
            if self.mode in ('host','client'):
                ui.delay(self._poll, 0.1)

    def _send(self, s, obj):
        try:
            s.sendall((json.dumps(obj) + "\n").encode('utf-8')); return True
        except Exception as e:
            self.log("[NET] send err: " + err_str(e))
            try: s.close()
            except: pass
            return False

    def _recv_sock(self, s, host_side):
        try:
            chunk = s.recv(4096)
            if not chunk: raise ConnectionError("peer closed")
            buf = self._bufs.get(s, b"") + chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_msg(s, line, host_side)
            self._bufs[s] = buf
        except BlockingIOError:
            return
        except Exception as e:
            self.log("[NET] recv err: " + err_str(e))
            try: s.close()
            except: pass
            if host_side:
                self._clients = [(x,a) for x,a in self._clients if x != s]

    def _handle_msg(self, s, line, host_side):
        try:
            msg = json.loads(line.decode('utf-8'))
        except:
            return
        code = msg.get("code")
        if (code or "") != (self.code or "") and msg.get("t") in ("hello","cmd","sync"):
            if host_side and msg.get("t")=="hello" and not self.code:
                self.code = code or ""
            elif host_side:
                self._send(s, {"t":"err","e":"bad-code"})
                return

        if host_side:
            t = msg.get("t")
            if t == "hello":
                self._send(s, {"t":"ok"})
            elif t == "cmd":
                c = msg.get("c","")
                if c: self.on_cmd(c)
            elif t == "sync":
                pass
        else:
            self.on_sync(msg)

# ========== simulátor sítě ==========
class SimEngine:
    def __init__(self, log_cb, diff):
        self.log = log_cb; self.diff = diff
        self.win = 120; self.baseline_rate = 150
        self.ddos_active=False; self.ddos_rate=0; self.ddos_target=None
        self.rate_limit=None
        self.req_history=collections.deque(maxlen=self.win)
        self.uniq_history=collections.deque(maxlen=self.win)
        self.syn_history=collections.deque(maxlen=self.win)
        self.ack_history=collections.deque(maxlen=self.win)
        self.arp_table={}; self.arp_spoof_on=False; self.arp_spoof_ip=None
        self.clean_stable_ticks=0
        self._seed_arp()

    def _seed_arp(self):
        for i in range(2,22):
            ip=f"192.168.0.{i}"; mac="AA:BB:CC:DD:EE:{:02X}".format(i)
            self.arp_table[ip]=mac

    def ddos_start(self, rate, target):
        self.ddos_active=True
        self.ddos_rate=max(1, int(rate * self.diff.ddos_mult))
        self.ddos_target=target
        self.log("[NET-SIM] DDoS emulace zapnuta")

    def ddos_stop(self):
        self.ddos_active=False; self.ddos_rate=0; self.ddos_target=None
        self.log("[NET-SIM] DDoS emulace vypnuta")

    def set_rate_limit(self, limit):
        if limit is None:
            self.rate_limit=None; self.log("[FIREWALL] Rate-limit zrušen")
        else:
            self.rate_limit=max(1,int(limit)); self.log(f"[FIREWALL] Rate-limit {self.rate_limit} req/s")

    def arp_spoof_enable(self, target_ip):
        self.arp_spoof_on=True; self.arp_spoof_ip=target_ip
        self.arp_table[target_ip]="FA:KE:FA:KE:FA:KE"
        self.log(f"[NET-SIM] ARP spoof emulace na {target_ip}")

    def arp_spoof_disable(self):
        if self.arp_spoof_on and self.arp_spoof_ip:
            i=int(self.arp_spoof_ip.split(".")[-1])
            self.arp_table[self.arp_spoof_ip]="AA:BB:CC:DD:EE:{:02X}".format(i)
        self.arp_spoof_on=False; self.log("[SEC] ARP spoof emulace vypnuta")

    def tick(self):
        base=int(max(50, random.gauss(self.baseline_rate,10)))
        add=self.ddos_rate if self.ddos_active else 0
        eff=base+add
        if self.rate_limit is not None: eff=min(eff,self.rate_limit)
        uniq=min(60, base//3)+(min(self.ddos_rate//50,4096) if self.ddos_active else 0)
        syn=max(1,int(eff*(0.5+(0.3 if self.ddos_active else 0.0)+random.uniform(-0.05,0.05))))
        ack=max(1,int(eff*(0.5-(0.2 if self.ddos_active else 0.0)+random.uniform(-0.05,0.05))))
        self.req_history.append(eff); self.uniq_history.append(int(uniq))
        self.syn_history.append(syn); self.ack_history.append(ack)
        verdict,_=self.detect_ddos()
        self.clean_stable_ticks=0 if verdict else min(9999,self.clean_stable_ticks+1)

    def snapshot(self, window=5):
        w=max(1,min(window,len(self.req_history)))
        req=sum(list(self.req_history)[-w:])/w
        uniq=sum(list(self.uniq_history)[-w:])/w
        syn=sum(list(self.syn_history)[-w:])
        ack=sum(list(self.ack_history)[-w:])
        ratio=syn/max(1,ack)
        return {"req_s":int(req), "uniq_src":int(uniq), "syn_ack":round(ratio,2)}

    def detect_ddos(self):
        snap=self.snapshot(10); s=self.diff.detect_sensitivity
        high=snap["req_s"]>(self.baseline_rate*4*s)
        many=snap["uniq_src"]>(300*s)
        ratio=snap["syn_ack"]>(2.5*s)
        return (high or many or ratio), snap

    def arp_status(self):
        col=[]
        for ip,mac in self.arp_table.items():
            if ip==self.arp_spoof_ip and self.arp_spoof_on and mac=="FA:KE:FA:KE:FA:KE":
                col.append((ip,mac))
        return col

    def series_req(self,n=60): return list(self.req_history)[-n:]
    def series_ratio(self,n=60):
        syn=list(self.syn_history)[-n:]; ack=list(self.ack_history)[-n:]; out=[]
        for i in range(len(syn)):
            a=ack[i] if i<len(ack) and ack[i]>0 else 1
            out.append(syn[i]/float(a))
        return out

# ========== Phishing simulátor ==========
class MailSim:
    def __init__(self):
        self.inbox=[
            {"id":"M-100","from":"hr@internal.test","subject":"Rozpis směn","body":"Ahoj, přikládám přepracovaný rozpis směn."},
            {"id":"M-1337","from":"no-reply@secure-update.test","subject":"Nutná změna hesla","body":"Vaše heslo vyprší. Ověřte účet na https://secure-update.test/reset.","phish":True},
            {"id":"M-205","from":"it@corp.test","subject":"Údržba VPN","body":"V pátek 22:00 krátká odstávka VPN."},
        ]
        self.flagged=set(); self.blocked_domains=set()
    def list_inbox(self):
        return [f"{m['id']}  From: {m['from']}   Subj: {m['subject']}" for m in self.inbox]
    def view(self, mid):
        for m in self.inbox:
            if m["id"]==mid: return f"{mid} | {m['from']} | {m['subject']}\n---\n{m.get('body','')}"
        return "Email nenalezen."
    def flag(self,mid): self.flagged.add(mid); return f"Zpráva {mid} označena."
    def block(self,domain): self.blocked_domains.add(domain); return f"Doména {domain} blokována."
    def is_phish(self,mid):
        for m in self.inbox:
            if m["id"]==mid: return bool(m.get("phish",False))
        return False

# ========== graf ==========
class GraphView(ui.View):
    def __init__(self):
        super().__init__()
        self.req=[]; self.ratio=[]; self.bg_color=(0.08,0.08,0.08)
    def update_data(self, req_list, ratio_list):
        self.req=req_list[:] if req_list else []; self.ratio=ratio_list[:] if ratio_list else []
        self.set_needs_display()
    def draw(self):
        if not self.req and not self.ratio: return
        inset=6; w=max(1,self.width-2*inset); h=max(1,self.height-2*inset); x0=inset; y0=inset
        req_max=max(1, max(self.req) if self.req else 1)
        rat_max=max(1.0, max(self.ratio) if self.ratio else 1.0)
        def to_pts(series,smax):
            n=len(series); 
            if n<=1: return []
            return [(x0+(i/(n-1.0))*w, y0+h - (clamp(series[i]/smax,0.0,1.0))*h) for i in range(n)]
        ui.set_color((0.18,0.18,0.18))
        for frac in (0.25,0.5,0.75):
            y=y0+h*(1.0-frac); p=ui.Path(); p.move_to(x0,y); p.line_to(x0+w,y); p.stroke()
        ui.set_color((1,1,1))
        pts=to_pts(self.req, req_max)
        if len(pts)>=2:
            p=ui.Path(); p.move_to(*pts[0]); [p.line_to(*pt) for pt in pts[1:]]; p.stroke()
        ui.set_color((0.4,0.7,1.0))
        pts2=to_pts(self.ratio, rat_max)
        if len(pts2)>=2:
            p2=ui.Path(); p2.move_to(*pts2[0]); [p2.line_to(*pt) for pt in pts2[1:]]; p2.stroke()
        ui.set_color((0.85,0.85,0.85))
        ui.draw_string(f"req/s max≈{int(req_max)}   SYN/ACK max≈{round(rat_max,2)}",
                       rect=(x0+4,y0+4,w-8,16), font=('Menlo',10), color=(0.85,0.85,0.85))

# ========== game state ==========
class GameState:
    def __init__(self, mission, ui_adapter, diff_name="Normal", on_push_state=None):
        self.mission=mission; self.ui=ui_adapter; self.diff=get_diff(diff_name)
        self.time_total=int(self.mission.time_limit * self.diff.time_mult)
        self.time_left=self.time_total; self.score=0; self.finished=False
        self.cmd_history=[]; self.hist_idx=-1; self.step_idx=0; self._tick_scheduled=False
        self._suppress_user_log=False
        self.on_push_state = on_push_state or (lambda: None)

        self.sim=SimEngine(self.ui.log, self.diff)
        self.mail = MailSim() if mission.code=="P1" else None

        # další stavy
        self.fs_alerted=False; self.isolated_nodes=set(); self.restored_snapshot=None
        self.repo_updates={}; self.repo_quarantine=set(); self.sig_verified=set()
        self.audit_loaded=False; self.accounts_disabled=set()

        # multiplayer
        self.net = NetPeer(self._on_net_cmd, self._on_net_sync, self.ui.log)
        self._orig_log = self.ui.log
        def _relay_log(s, user=False):
            self._orig_log(s, user)
            if self.net and self.net.is_host():
                try: self.net.broadcast({"t":"log","s":s})
                except Exception as e: print("[ERR] broadcast log:", err_str(e))
        self.ui.log = _relay_log

        if mission.code=="D3": self.sim.arp_spoof_enable("192.168.0.5")

        self.ui.log(f"[SYS] Mise '{mission.name}' – role: {mission.role}. Limit: {self.time_total}s")
        self.ui.log(f"[SYS] Obtížnost: {self.diff.name}")
        self.ui.log("[SYS] Napiš 'help' pro nápovědu.")
        self.ui.log(f"[SYS] První krok: {mission.steps[0].title}")
        self._update_all_ui(); self._schedule_tick()

    # --- snapshot pro sync ---
    def _snapshot(self):
        return {"mission": self.mission.code, "step": self.step_idx,
                "score": self.score, "time_left": self.time_left, "ids": self._ids_state()}

    def _push_state(self):
        try:
            if self.net and self.net.is_host():
                self.net.broadcast({"t":"state","v": self._snapshot()})
        finally:
            try: self.on_push_state()
            except: pass

    # --- callbacks net ---
    def _on_net_cmd(self, txt):
        try:
            self._orig_log(f"[REMOTE] > {txt}")
            self._suppress_user_log=True
            self.submit(txt)
        finally:
            self._suppress_user_log=False

    def _on_net_sync(self, payload):
        try:
            t=payload.get("t")
            if t=="state":
                v=payload.get("v",{})
                self.time_left=v.get("time_left",self.time_left)
                self.score=v.get("score",self.score)
                self.step_idx=min(self.step_idx, v.get("step",self.step_idx))
                self._update_all_ui()
            elif t=="log":
                self._orig_log(payload.get("s",""))
        except Exception as e:
            self._orig_log("[NET] sync err: " + err_str(e))

    # --- tick smyčka ---
    def _schedule_tick(self):
        if self.finished or self._tick_scheduled: return
        self._tick_scheduled=True; ui.delay(self._on_tick, 1.0)

    def _on_tick(self):
        self._tick_scheduled=False
        if self.finished: return
        try: self.sim.tick()
        except Exception as e: self.ui.log("[ERR] tick: " + err_str(e))
        self.time_left -= 1
        if self.time_left <= 0:
            self.finished=True; self.ui.log("[SYS] Čas vypršel.")
            self.ui.finish(self.score, self.time_left, self._rank()); self._update_header(); return
        self._update_all_ui(); self._push_state(); self._schedule_tick()

    # --- UI updates ---
    def _ids_state(self):
        verdict,_=self.sim.detect_ddos()
        if verdict and self.sim.ddos_active: return "ATTACK"
        if verdict: return "SUSPECT"
        return "CLEAN"
    def _update_all_ui(self):
        self._update_header(); self._update_ids_step(); self._update_graph()
    def _update_header(self):
        self.ui.header(self.time_left, self.score, self.time_total)
    def _update_ids_step(self):
        step_str=f"Krok {self.step_idx+1}/{len(self.mission.steps)}"
        self.ui.ids_step(self._ids_state(), step_str)
    def _update_graph(self):
        self.ui.graph(self.sim.series_req(60), self.sim.series_ratio(60))

    # --- help/hint ---
    def _hint_text(self, step):
        return step.hint if self.diff.hint_level>=2 else f"Cíl: {step.title}"

    # --- příkazy ---
    def submit(self, text):
        if self.finished: return
        text=(text or "").strip()
        if not text: return
        if not self._suppress_user_log: self.ui.log("> " + text, user=True)
        self.cmd_history.append(text); self.hist_idx=len(self.cmd_history)

        parts=text.split(); base=parts[0]; args={}; i=1
        while i<len(parts):
            p=parts[i]
            if p.startswith("--"):
                key=p[2:]; val=True
                if i+1<len(parts) and not parts[i+1].startswith("--"):
                    val=parts[i+1]; i+=1
                args[key]=val
            else:
                args.setdefault("_pos",[]).append(p)
            i+=1

        # Selftest povolen vždy
        if base=="selftest":
            self.run_selftest(); self._update_header(); return

        # pokud jsme klient → poslat hostu a lokálně nespouštět (kromě net příkazů)
        if self.net and self.net.is_client() and base!="net":
            self.net.send_cmd(text); return

        if base=="help":
            self._cmd_help(); self._update_header(); return
        if base not in self.mission.allowed and base!="net":
            self.ui.log("[SYS] Neznámý/zakázaný příkaz pro tuto misi."); return

        try:
            self._dispatch(base, args)
        except Exception as e:
            self.ui.log("[ERR] cmd: " + err_str(e))

        # validace kroku (pokud to nebyl net příkaz)
        if base!="net":
            st=self.mission.steps[self.step_idx]; ok=False
            try:
                ok=(st.validator_fn(base,args,self) if st.validator_fn else (base==st.validator))
            except Exception as e:
                self.ui.log("[ERR] validator: " + err_str(e)); ok=False

            if ok:
                self.score += int(100 * self.diff.score_mult)
                self.ui.log("OK: " + st.success_log); self._advance()
            else:
                self.score = max(0, self.score - int(self.diff.penalty))
                self.ui.log(f"[SYS] Mimo cíl. (-{int(self.diff.penalty)})")

            self._update_all_ui(); self._push_state()

    def _dispatch(self, cmd, args):
        # Společné
        if cmd=="status":
            state=self._ids_state(); snap=self.sim.snapshot(10)
            self.ui.log(f"[SYS] Krok {self.step_idx+1}/{len(self.mission.steps)} | Čas {self.time_left}s | Skóre {self.score}")
            self.ui.log(f"[NET] req/s≈{snap['req_s']} uniq≈{snap['uniq_src']} SYN/ACK≈{snap['syn_ack']} | IDS {state}")
        elif cmd=="scan":
            snap=self.sim.snapshot(3); verdict,_=self.sim.detect_ddos()
            self.ui.log(f"[RADIO-SIM] Scan: req/s≈{snap['req_s']} uniq≈{snap['uniq_src']}")
            self.ui.log("[IDS] baseline OK; anomalies=0" if not verdict else "[IDS] traffic anomaly")
        elif cmd=="log":
            snap=self.sim.snapshot(10)
            self.ui.log(f"[LOG] t={time.strftime('%H:%M:%S')} SYN/ACK≈{snap['syn_ack']} req/s≈{snap['req_s']}")
            if self.sim.ddos_active: self.ui.log("[LOG] Mitigation hint: rate-limit (emulated)")
        elif cmd=="inject":
            self.ui.log(f"[RADIO-SIM] Test paket doručen (target={args.get('target','N/A')}); integrity OK")
        elif cmd=="clear":
            self.ui.log("[SYS] Obrazovka vyčištěna.")
        elif cmd=="map":
            self.ui.log("[GRID-SIM] Nodes: N1..N6; links coherent")
        elif cmd=="heartbeat":
            self.ui.log(f"[GRID-SIM] Heartbeat sent → {args.get('node','N3')} | ack OK")

        # DDoS
        elif cmd=="ddos":
            if args.get("check",False):
                verdict,snap=self.sim.detect_ddos()
                self.ui.log(f"[IDS] req/s≈{snap['req_s']} uniq≈{snap['uniq_src']} SYN/ACK≈{snap['syn_ack']}")
                self.ui.log("[IDS] DDoS SUSPECTED" if verdict else "[IDS] No anomalies")
            elif args.get("stop",False):
                self.sim.ddos_stop(); self.ui.log("[IDS] Čekám na stabilní čistotu (5s)...")
            elif "rate" in args or "target" in args:
                rate=as_int(args.get("rate",1000),1000); tgt=args.get("target","base-ops")
                self.sim.ddos_start(rate,tgt); self.ui.log(f"[NET-SIM] Sim load active: rate={rate} target={tgt}")
            else:
                self.ui.log("[SYS] ddos --check | --rate <n> --target <name> | --stop")

        elif cmd=="mitigate":
            mode=args.get("mode","")
            if mode=="rate-limit":
                limit=as_int(args.get("limit",100),100); self.sim.set_rate_limit(limit)
            elif mode=="off":
                self.sim.set_rate_limit(None)
            else:
                self.ui.log("[SYS] mitigate --mode rate-limit --limit <n> | --mode off")

        # ARP
        elif cmd=="arp":
            if args.get("list",False):
                col=self.sim.arp_status()
                for ip,mac in sorted(self.sim.arp_table.items()):
                    tag=" !COLLISION" if any(ip==c[0] for c in col) else ""
                    self.ui.log(f"[NET-SIM] {ip} → {mac}{tag}")
            elif args.get("verify",False):
                col=self.sim.arp_status()
                self.ui.log("[NET-SIM] ARP collisions: "+", ".join(ip for ip,_ in col) if col else "[NET-SIM] ARP table clean")
            else:
                self.ui.log("[SYS] arp --list | --verify")
        elif cmd=="counter":
            target=args.get("target","")
            if target and target==self.sim.arp_spoof_ip and self.sim.arp_spoof_on:
                self.sim.arp_spoof_disable(); self.ui.log(f"[SEC] ARP spoof neutralised on {target}")
            else:
                self.ui.log("[SEC] No action (target clean)")

        # Mail
        elif cmd=="mail":
            if not self.mail: self.ui.log("[SYS] Mail není součástí této mise."); return
            if args.get("inbox",False):
                for line in self.mail.list_inbox(): self.ui.log("[MAIL] "+line)
            elif args.get("view",False):
                self.ui.log("[MAIL] "+self.mail.view(args.get("view","")))
            elif args.get("flag",False):
                self.ui.log("[MAIL] "+self.mail.flag(args.get("flag","")))
            elif args.get("block",False):
                self.ui.log("[MAIL] "+self.mail.block(args.get("block","")))
            else:
                self.ui.log("[SYS] mail --inbox | --view <id> | --flag <id> | --block <domain>")

        # Ransomware
        elif cmd=="fs":
            if args.get("monitor",False):
                self.fs_alerted=True
                self.ui.log("[FS] suspicious encryption spike on node-2 (entropy↑, iops↑)")
                self.ui.log("[FS] unusual file rename patterns *.locked")
            else:
                self.ui.log("[SYS] fs --monitor")
        elif cmd=="isolate":
            node=args.get("node","")
            if node:
                self.isolated_nodes.add(node); self.ui.log(f"[NET] Node {node} isolated (network quarantine)")
            else:
                self.ui.log("[SYS] isolate --node <name>")
        elif cmd=="restore":
            snap=args.get("snapshot","")
            if snap:
                self.restored_snapshot=snap; self.ui.log(f"[FS] Restore initiated from snapshot '{snap}' (emulated)")
            else:
                self.ui.log("[SYS] restore --snapshot <name>")

        # Supply-chain
        elif cmd=="repo":
            if args.get("update",False):
                name=args.get("update",""); state="invalid" if name=="repoX" else "ok"
                self.repo_updates[name]=state
                self.ui.log(f"[REPO] Update {name} downloaded – {'signature mismatch' if state=='invalid' else 'signature OK'}")
            elif args.get("quarantine",False):
                name=args.get("quarantine",""); self.repo_quarantine.add(name); self.ui.log(f"[REPO] {name} quarantined")
            else:
                self.ui.log("[SYS] repo --update <name> | --quarantine <name>")
        elif cmd=="verify":
            if args.get("sig",False):
                name=args.get("sig",""); self.sig_verified.add(name); state=self.repo_updates.get(name,"unknown")
                if state=="invalid": self.ui.log(f"[VERIFY] {name}: SIGNATURE INVALID")
                elif state=="ok": self.ui.log(f"[VERIFY] {name}: signature valid")
                else: self.ui.log(f"[VERIFY] {name}: no update metadata")
            else:
                self.ui.log("[SYS] verify --sig <name>")

        # Insider
        elif cmd=="audit":
            if args.get("list",False):
                self.audit_loaded=True
                self.ui.log("[AUDIT] user=bob cmd='rm -rf /secure' from=10.0.0.23 at=03:14")
                self.ui.log("[AUDIT] user=alice cmd='kubectl get secrets' from=10.0.0.42 at=03:16")
            else:
                self.ui.log("[SYS] audit --list")
        elif cmd=="account":
            if args.get("disable",False):
                user=args.get("disable","")
                if user: self.accounts_disabled.add(user); self.ui.log(f"[IAM] account {user} disabled")
                else: self.ui.log("[SYS] account --disable <user>")
            else:
                self.ui.log("[SYS] account --disable <user>")

        # Multiplayer příkaz
        elif cmd=="net":
            if args.get("host",False):
                port=as_int(args.get("port",50555),50555); code=str(args.get("code",""))
                ok,msg=self.net.host(port=port, code=code); self.ui.log("[NET] "+msg)
            elif args.get("join",False):
                host=args.get("host",""); port=as_int(args.get("port",50555),50555); code=str(args.get("code",""))
                ok,msg=self.net.join(host=host, port=port, code=code); self.ui.log("[NET] "+msg)
                if ok: self.net.request_sync()
            elif args.get("who",False):
                self.ui.log("[NET] Peers: "+", ".join(self.net.list_peers()))
            elif args.get("leave",False):
                self.net.leave(); self.ui.log("[NET] Odpojeno")
            else:
                self.ui.log("[SYS] net --host --port 50555 --code <pin> | net --join --host <ip> --port 50555 --code <pin> | net --who | net --leave")

        elif cmd=="patch":
            self.ui.log(f"[FIREWALL] Rule '{args.get('rule','drop-noise')}' applied")

    # --- pomocné / historie / help ---
    def _cmd_help(self):
        allowed=", ".join(sorted(set(self.mission.allowed+['selftest','net'])))
        cur=self.mission.steps[self.step_idx]
        self.ui.log(f"[HELP] Dostupné příkazy: {allowed}")
        self.ui.log(f"[HELP] {self._hint_text(cur)}")

    def history_prev(self):
        if not self.cmd_history: return ""
        self.hist_idx=max(0,self.hist_idx-1); return self.cmd_history[self.hist_idx]
    def history_next(self):
        if not self.cmd_history: return ""
        self.hist_idx=min(len(self.cmd_history), self.hist_idx+1)
        return "" if self.hist_idx==len(self.cmd_history) else self.cmd_history[self.hist_idx]

    def _advance(self):
        if self.step_idx+1 < len(self.mission.steps):
            self.step_idx+=1; nxt=self.mission.steps[self.step_idx]
            self.ui.log(f"[SYS] Další krok: {nxt.title}")
            self.ui.log(f"[SYS] Nápověda: {self._hint_text(nxt)}")
            self._update_all_ui(); self._push_state()
        else:
            self.finished=True; self.ui.log("[SYS] Mise splněna!")
            self.ui.finish(self.score, self.time_left, self._rank()); self._push_state()

    def _rank(self):
        s=self.score
        if s>=700: return "Cyber Major"
        if s>=400: return "Operator"
        if s>=200: return "Recruit"
        return "Cadet"

    # --- SELFTEST ---
    def _plan_for_mission(self):
        d=self.diff.name
        wait_mid = 0.8 if d in ("Easy","Normal") else 1.2
        wait_long= 5.5 if d in ("Easy","Normal") else 6.5
        c=self.mission.code
        if c=="A1": return [("scan --passive",wait_mid), ("inject --target N2",wait_mid)]
        if c=="D1": return [("scan",wait_mid), ("mitigate --mode rate-limit --limit 100",wait_mid)]
        if c=="A3": return [("ddos --check",wait_mid), ("ddos --rate 5000 --target base-ops",wait_mid),
                            ("mitigate --mode rate-limit --limit 120",wait_long), ("ddos --stop",wait_mid)]
        if c=="D3": return [("arp --list",wait_mid), ("counter --target 192.168.0.5",wait_mid), ("arp --verify",wait_mid)]
        if c=="P1": return [("mail --inbox",wait_mid), ("mail --flag M-1337",wait_mid), ("mail --block secure-update.test",wait_mid)]
        if c=="R1": return [("fs --monitor",wait_mid), ("isolate --node node-2",wait_mid), ("restore --snapshot pre-incident",wait_mid)]
        if c=="S1": return [("repo --update repoX",wait_mid), ("verify --sig repoX",wait_mid), ("repo --quarantine repoX",wait_mid)]
        if c=="I1": return [("audit --list",wait_mid), ("account --disable bob",wait_mid)]
        return []
    def run_selftest(self):
        if getattr(self,"_st_running",False): self.ui.log("[SELFTEST] Už běží."); return
        plan=self._plan_for_mission()
        if not plan: self.ui.log("[SELFTEST] Pro tuto misi není plán."); return
        self.ui.log(f"[SELFTEST] Start · {self.mission.code} · {self.diff.name}")
        self._st_running=True; self._st_queue=list(plan)
        self._st_score_start=self.score; self._st_step_start=self.step_idx
        self._st_tick()
    def _st_tick(self):
        if self.finished: self._st_finish(); return
        if not self._st_queue: ui.delay(self._st_finish,0.6); return
        cmd,delay_s=self._st_queue.pop(0)
        self._suppress_user_log=True; self.ui.log(f"[AUTO] > {cmd}")
        try: self.submit(cmd)
        finally: self._suppress_user_log=False
        ui.delay(self._st_tick, max(0.05,float(delay_s)))
    def _st_finish(self):
        ok=self.finished or (self.step_idx>=len(self.mission.steps))
        delta=self.score - getattr(self,"_st_score_start",self.score)
        self.ui.log(f"[SELFTEST] {self.mission.code} ... {'PASS' if ok else 'FAIL'}  (+{delta} bodů)")
        self._st_running=False

# ========== mise ==========
def missions_all():
    MissionStep_ = MissionStep
    a1 = Mission("A1","RADIO-SIM: Link Probe (Attack)",
        "Zjisti parametry linky (pasivně) a vlož test paket na N2.","Attack",
        ["help","scan","status","log","inject","clear","map","heartbeat"],
        [MissionStep_("Zmapuj linku pasivně.","Použij: scan --passive","scan","Pasivní scan hotov.",
                      validator_fn=lambda b,a,gs: b=="scan" and a.get("passive",False)),
         MissionStep_("Vlož test paket na N2.","Použij: inject --target N2","inject","Test paket na N2 potvrzen.",
                      validator_fn=lambda b,a,gs: b=="inject" and a.get("target","")=="N2")],
        120)
    d1 = Mission("D1","RADIO-SIM: Noise Mitigation (Defend)",
        "Diagnostika a mitigace šumu firewall pravidlem.","Defend",
        ["help","scan","status","log","patch","clear","mitigate"],
        [MissionStep_("Zkontroluj stav linky.","Použij: scan","scan","Diagnostika hotová."),
         MissionStep_("Aplikuj rate-limit.","Použij: mitigate --mode rate-limit --limit 100","mitigate","Rate-limit aktivní.",
                      validator_fn=lambda b,a,gs: b=="mitigate" and a.get("mode","")=="rate-limit" and str(a.get("limit",""))=="100")],
        120)
    a3 = Mission("A3","NET-SIM: DDoS Drill (Attack)",
        "Baseline, zátěž, mitigace do stabilna (5s), korektní stop.","Attack",
        ["help","ddos","status","scan","log","clear","mitigate","net","selftest"],
        [MissionStep_("Ověř baseline IDS bez anomálií.","Použij: ddos --check","ddos","Baseline bez anomálií.",
                      validator_fn=lambda b,a,gs: b=="ddos" and a.get("check",False) and (not gs.sim.detect_ddos()[0])),
         MissionStep_("Spusť zátěž na base-ops.","Použij: ddos --rate 5000 --target base-ops","ddos","Emulace DDoS běží.",
                      validator_fn=lambda b,a,gs: b=="ddos" and str(a.get("rate",""))=="5000" and a.get("target","")=="base-ops" and gs.sim.ddos_active),
         MissionStep_("Mitiguj na ≤120 req/s a udrž 5s clean.","Použij: mitigate --mode rate-limit --limit 120","mitigate","Metriky pod prahem a stabilní.",
                      validator_fn=lambda b,a,gs: b=="mitigate" and a.get("mode","")=="rate-limit" and (as_int(a.get("limit",9999),9999)<=120)),
         MissionStep_("Korektně ukonči zátěž a ověř clean.","Použij: ddos --stop","ddos","Zátěž ukončena, IDS clean.",
                      validator_fn=lambda b,a,gs: b=="ddos" and a.get("stop",False) and gs.sim.clean_stable_ticks>=5)],
        200)
    d3 = Mission("D3","NET-SIM: ARP Defense (Defend)",
        "Najdi kolizi v ARP, zneutralizuj a ověř čistotu.","Defend",
        ["help","arp","counter","status","log","clear","net","selftest"],
        [MissionStep_("Zobraz ARP tabulku s kolizí.","Použij: arp --list","arp","Kolize potvrzena.",
                      validator_fn=lambda b,a,gs: b=="arp" and a.get("list",False) and len(gs.sim.arp_status())>0),
         MissionStep_("Zastav spoofing cíleně.","Použij: counter --target 192.168.0.5","counter","Spoofing zablokován.",
                      validator_fn=lambda b,a,gs: b=="counter" and a.get("target","")=="192.168.0.5"),
         MissionStep_("Ověř čistotu ARP tabulky.","Použij: arp --verify","arp","Tabulka čistá.",
                      validator_fn=lambda b,a,gs: b=="arp" and a.get("verify",False) and len(gs.sim.arp_status())==0)],
        180)
    p1 = Mission("P1","SOC-SIM: Phishing Drill (Defend)",
        "Najdi podvodný mail, označ ho a zablokuj doménu odesílatele.","Defend",
        ["help","status","log","clear","mail","net","selftest"],
        [MissionStep_("Otevři inbox a projdi zprávy.","Použij: mail --inbox","mail","Inbox zobrazen.",
                      validator_fn=lambda b,a,gs: b=="mail" and a.get("inbox",False)),
         MissionStep_("Označ phishing zprávu.","Použij: mail --flag M-1337","mail","Phish označen.",
                      validator_fn=lambda b,a,gs: b=="mail" and a.get("flag","")=="M-1337" and gs.mail and gs.mail.is_phish("M-1337")),
         MissionStep_("Zablokuj odesílatele (doménu).","Použij: mail --block secure-update.test","mail","Doména blokována.",
                      validator_fn=lambda b,a,gs: b=="mail" and a.get("block","")=="secure-update.test")],
        160)
    r1 = Mission("R1","SOC-SIM: Ransomware Response (Defend)",
        "Odhal šifrovací aktivitu, izoluj uzel a obnov data ze snapshotu.","Defend",
        ["help","status","log","clear","fs","isolate","restore","net","selftest"],
        [MissionStep_("Zachyť šifrovací aktivitu.","Použij: fs --monitor","fs","Aktivita potvrzena.",
                      validator_fn=lambda b,a,gs: b=="fs" and a.get("monitor",False)),
         MissionStep_("Izoluj postižený uzel.","Použij: isolate --node node-2","isolate","Uzel izolován.",
                      validator_fn=lambda b,a,gs: b=="isolate" and a.get("node","") in ("node-2","N2")),
         MissionStep_("Obnov data ze snapshotu.","Použij: restore --snapshot pre-incident","restore","Obnova spuštěna.",
                      validator_fn=lambda b,a,gs: b=="restore" and a.get("snapshot","")=="pre-incident")],
        180)
    s1 = Mission("S1","SUPPLY: Tainted Update (Defend)",
        "Zachyť vadný update, ověř podpis a repo dej do karantény.","Defend",
        ["help","status","log","clear","repo","verify","net","selftest"],
        [MissionStep_("Stáhni update a sleduj výsledek.","Použij: repo --update repoX","repo","Update přijat.",
                      validator_fn=lambda b,a,gs: b=="repo" and a.get("update","")=="repoX"),
         MissionStep_("Ověř podpis problematického balíčku.","Použij: verify --sig repoX","verify","Podpis ověřen.",
                      validator_fn=lambda b,a,gs: b=="verify" and a.get("sig","")=="repoX"),
         MissionStep_("Repo dej do karantény.","Použij: repo --quarantine repoX","repo","Repo karanténa.",
                      validator_fn=lambda b,a,gs: b=="repo" and a.get("quarantine","")=="repoX")],
        150)
    i1 = Mission("I1","INSIDER: Suspicious Activity (Defend)",
        "Prohlédni audit log a dočasně deaktivuj podezřelý účet.","Defend",
        ["help","status","log","clear","audit","account","net","selftest"],
        [MissionStep_("Prohledej audit log.","Použij: audit --list","audit","Audit načten.",
                      validator_fn=lambda b,a,gs: b=="audit" and a.get("list",False)),
         MissionStep_("Deaktivuj účet bob.","Použij: account --disable bob","account","Účet zablokován.",
                      validator_fn=lambda b,a,gs: b=="account" and a.get("disable","")=="bob")],
        140)
    return [a1,d1,a3,d3,p1,r1,s1,i1]

# ========== UI ==========
class ProgressBar(ui.View):
    def __init__(self):
        super().__init__(); self._p=1.0
        self.bg=ui.View(background_color=(0.25,0.25,0.25))
        self.fg=ui.View(background_color=(0.2,0.7,0.3))
        self.add_subview(self.bg); self.add_subview(self.fg)
    def set_progress(self,p):
        self._p=max(0.0,min(1.0,float(p)))
        try:
            w=self.width if self.width>1 else 1; h=self.height if self.height>1 else 1
            self.fg.frame=(0,0,self._p*w,h)
        except Exception as e: print("[ERR] progressbar:", err_str(e))
    def layout(self):
        self.bg.frame=(0,0,self.width,self.height); self.set_progress(self._p)

class UIAdapter:
    def __init__(self, app): self.app=app
    def log(self, s, user=False):
        try:
            self.app.console.text += s + "\n"; _scroll_tv_to_end(self.app.console)
        except Exception as e: print("[ERR] ui-log:", err_str(e), "|", repr(s))
    def header(self, t, score, total):
        try:
            self.app.header.text=f"Čas: {max(0,t):>3}s    Skóre: {score:>4}"
            self.app.time_bar.set_progress(float(max(0,t))/max(1,total))
        except Exception as e: print("[ERR] header:", err_str(e))
    def ids_step(self, ids_label, step_str):
        self.app.step_lbl.text=step_str; self.app.ids_lbl.text="IDS: "+ids_label
        if ids_label=="CLEAN": self.app.ids_lbl.text_color=(0.6,1.0,0.6)
        elif ids_label=="SUSPECT": self.app.ids_lbl.text_color=(1.0,0.9,0.5)
        else: self.app.ids_lbl.text_color=(1.0,0.5,0.5)
    def graph(self, req, ratio): self.app.graph.update_data(req, ratio)
    def finish(self, score, t, rank):
        self.log(f"[SYS] Konec. Skóre {score}, zbylý čas {t}s, hodnost: {rank}")

class App(ui.View):
    def __init__(self):
        super().__init__(); self.background_color='black'
        self.missions=missions_all(); self.state=None; self.diff='Normal'

        # header
        self.title_lbl=ui.Label(text="CyberDrill 4.0", text_color='white', font=('Menlo',16))
        self.step_lbl=ui.Label(text_color='white', font=('Menlo',13))
        self.ids_lbl=ui.Label(text_color=(0.7,0.9,0.7), font=('Menlo',13))
        self.time_bar=ProgressBar(); self.header=ui.Label(text_color='white', font=('Menlo',14))

        # mission + diff
        self.picker=ui.SegmentedControl(); self.picker.segments=[m.code for m in self.missions]; self.picker.selected_index=0
        self.diff_ctrl=ui.SegmentedControl(); self.diff_ctrl.segments=['Easy','Normal','Hard','Insane']; self.diff_ctrl.selected_index=1
        def _on_diff(sender): self.diff=sender.segments[sender.selected_index]
        self.diff_ctrl.action=_on_diff

        # buttons
        self.btn_start=ui.Button(title='Start', action=self.on_start)
        self.btn_reset=ui.Button(title='Reset', action=self.on_reset)
        self.btn_help=ui.Button(title='Help', action=self.toggle_help)
        self.btn_selftest=ui.Button(title='SelfTest', action=self.on_selftest)
        self.btn_save=ui.Button(title='Save', action=self.on_save_log)
        self.btn_copy=ui.Button(title='Copy', action=self.on_copy_log)

        # graph
        self.graph=GraphView()

        # tabs
        self.tab=ui.SegmentedControl()
        self.tab.segments=['Console','Help','Net']
        self.tab.selected_index=0
        self.tab.action=self.on_tab

        # console + help view
        self.console=ui.TextView(); self.console.background_color=(0.05,0.05,0.05)
        self.console.text_color=(0.85,1.0,0.85); self.console.font=('Menlo',14); self.console.editable=False

        self.help_panel=ui.TextView(); self.help_panel.background_color=(0.08,0.08,0.08)
        self.help_panel.text_color=(0.85,0.85,0.85); self.help_panel.font=('Menlo',13)
        self.help_panel.editable=False; self.help_panel.hidden=True

        # NET panel
        self.net_panel=ui.View(background_color=(0.08,0.08,0.12)); self.net_panel.hidden=True
        self.ip_tf=ui.TextField(placeholder='host (IP)', text_color='white', background_color=(0.1,0.1,0.1))
        self.port_tf=ui.TextField(placeholder='port', text_color='white', background_color=(0.1,0.1,0.1))
        self.code_tf=ui.TextField(placeholder='code/PIN', text_color='white', background_color=(0.1,0.1,0.1))
        self.btn_host=ui.Button(title='Host', action=self.on_net_host)
        self.btn_join=ui.Button(title='Join', action=self.on_net_join)
        self.btn_leave=ui.Button(title='Leave', action=self.on_net_leave)
        self.peers_lbl=ui.Label(text='Peers: -', text_color='white', font=('Menlo',12))
        for v in [self.ip_tf,self.port_tf,self.code_tf,self.btn_host,self.btn_join,self.btn_leave,self.peers_lbl]:
            self.net_panel.add_subview(v)

        # quick + input
        self.quick=[ui.Button(title='') for _ in range(5)]
        for b in self.quick: b.action=self.on_quick
        self.btn_prev=ui.Button(title='◀︎', action=self.on_hist_prev)
        self.btn_next=ui.Button(title='▶︎', action=self.on_hist_next)
        self.btn_clearlog=ui.Button(title='Clear', action=self.on_clear_log)
        self.input=ui.TextField(); self.input.autocorrection_type=False
        try: self.input.autocapitalization_type=ui.AUTOCAPITALIZE_NONE
        except: pass
        self.input.font=('Menlo',14); self.input.text_color='white'; self.input.background_color=(0.1,0.1,0.1)
        self.input.clear_button_mode='while_editing'; self.input.action=self.on_submit
        self.btn_enter=ui.Button(title='Enter', action=self.on_submit_button)

        # add views
        for v in [self.title_lbl,self.step_lbl,self.ids_lbl,self.time_bar,self.header,
                  self.picker,self.diff_ctrl,self.btn_start,self.btn_reset,self.btn_help,self.btn_selftest,self.btn_save,self.btn_copy,
                  self.graph,self.tab,self.console,self.help_panel,self.net_panel,
                  *self.quick,self.btn_prev,self.btn_next,self.btn_clearlog,self.input,self.btn_enter]:
            self.add_subview(v)

        for v in [self.title_lbl,self.step_lbl,self.ids_lbl,self.time_bar,self.header,self.picker,self.diff_ctrl,self.graph,self.tab,self.console,self.help_panel,self.net_panel,self.input]:
            v.flex='W'
        self.console.flex='WH'; self.help_panel.flex='WH'; self.net_panel.flex='W'; self.graph.flex='W'

        self._init_header()
        self._populate_quick(None)

    def _init_header(self):
        self.header.text="Čas:   0s    Skóre:    0"; self.time_bar.set_progress(1.0)
        self._println("[SYS] Vyber misi (A1/D1/A3/D3/P1/R1/S1/I1), zvol obtížnost a dej Start.")
        self._println("[SYS] Záložky dole: Console / Help / Net. Export logu: Save / Copy.")

    def layout(self):
        W,H=self.width,self.height; pad=10; row=24; btn=70; small=64
        y=pad
        self.title_lbl.frame=(pad,y,W-2*pad,row); y+=row+2
        self.step_lbl.frame=(pad,y,(W-2*pad)//2,row); self.ids_lbl.frame=(pad+(W-2*pad)//2,y,(W-2*pad)//2,row); y+=row+2
        self.time_bar.frame=(pad,y,W-2*pad,6); y+=10
        self.header.frame=(pad,y,W-2*pad,row); y+=row+2

        # horní řádek: picker + diff + 6 tlačítek (Start/Reset/Help/SelfTest/Save/Copy)
        self.picker.frame=(pad,y,W-3*pad-6*btn-160,row)
        self.diff_ctrl.frame=(self.picker.x+self.picker.width+6,y,150,row)
        self.btn_start.frame=(W-pad-6*btn-2*pad,y,btn,row)
        self.btn_reset.frame=(W-pad-5*btn-1*pad,y,btn,row)
        self.btn_help.frame =(W-pad-4*btn,y,btn,row)
        self.btn_selftest.frame=(W-pad-3*btn,y,btn,row)
        self.btn_save.frame=(W-pad-2*btn,y,btn,row)
        self.btn_copy.frame=(W-pad-1*btn,y,btn,row)
        y+=row+6

        self.graph.frame=(pad,y,W-2*pad,120); y+=120+6

        self.tab.frame=(pad,y,220,row); y+=row+4
        area_h=int(H*0.34)
        self.console.frame=(pad,y,W-2*pad,area_h)
        self.help_panel.frame=(pad,y,W-2*pad,area_h)
        self.net_panel.frame=(pad,y,W-2*pad,area_h)
        # net panel layout
        nx=pad; ny=8
        self.ip_tf.frame=(nx,ny,180,28); self.port_tf.frame=(nx+186,ny,90,28); self.code_tf.frame=(nx+280,ny,120,28)
        self.btn_host.frame=(nx+406,ny,small,28); self.btn_join.frame=(nx+406+small+6,ny,small,28); self.btn_leave.frame=(nx+406+2*(small+6),ny,small,28)
        self.peers_lbl.frame=(nx, ny+34, W-2*pad-10, 24)

        y += area_h + 6

        qw=(W-2*pad-5*6)/5.0; qh=28
        for i,b in enumerate(self.quick): b.frame=(pad+i*(qw+6), y, qw, qh)
        y += qh + 6

        self.btn_prev.frame=(pad, y, 40, qh); self.btn_next.frame=(pad+44, y, 40, qh)
        self.btn_clearlog.frame=(pad+88, y, 60, qh)
        self.input.frame=(pad+152, y, W - (pad+152) - (pad+btn), qh)
        self.btn_enter.frame=(W-pad-btn, y, btn, qh)

        self.title_lbl.text = "CyberDrill 4.0" if not self.state else f"{self.state.mission.code} · {self.state.mission.name}"

    # helpers
    def _println(self, msg):
        try: self.console.text += msg + "\n"; _scroll_tv_to_end(self.console)
        except Exception as e: print("[ERR] println:", err_str(e))

    def will_close(self):
        try:
            if self.state: self.state.finished=True
        except Exception as e: print("[ERR] will_close:", err_str(e))

    # UI callbacks
    def on_start(self, sender):
        try:
            idx=max(0,self.picker.selected_index); m=self.missions[idx]
            if self.state: self.state.finished=True
            self.console.text=""; self.help_panel.hidden=False; self.net_panel.hidden=True; self.tab.selected_index=0
            self.title_lbl.text=f"{m.code} · {m.name}"
            self.state=GameState(m, UIAdapter(self), diff_name=self.diff, on_push_state=self._update_net_ui)
            self.help_panel.text=f"{m.name}\n\n{m.description}\n\nPovolené příkazy:\n- "+"\n- ".join(sorted(set(m.allowed+['selftest','net'])))
            self._populate_quick(m.code)
            self._update_net_ui()
        except Exception as e:
            self._println("[ERR] start: " + err_str(e))

    def on_reset(self, sender):
        if self.state: self.state.finished=True
        self.console.text=""; self.graph.update_data([], [])
        self._init_header(); self._println("[SYS] Resetováno. Zvol misi a dej Start.")
        self._populate_quick(None); self._update_net_ui()

    def on_submit(self, sender):
        try:
            if self.state:
                txt=self.input.text or ""; self.input.text=""; self.state.submit(txt)
        except Exception as e:
            self._println("[ERR] submit: " + err_str(e))
    def on_submit_button(self, sender): self.on_submit(sender)

    def toggle_help(self, sender):
        # jen přepni tab
        idx = self.tab.selected_index
        self.tab.selected_index = 1 if idx != 1 else 0
        self.on_tab(self.tab)

    def on_tab(self, sender):
        t = sender.segments[sender.selected_index]
        self.console.hidden = (t!='Console')
        self.help_panel.hidden = (t!='Help')
        self.net_panel.hidden = (t!='Net')

    def on_quick(self, sender):
        if self.state and sender.title: self.input.text = sender.title
    def on_hist_prev(self, sender):
        if self.state: self.input.text = self.state.history_prev()
    def on_hist_next(self, sender):
        if self.state: self.input.text = self.state.history_next()
    def on_clear_log(self, sender):
        self.console.text = ""

    # Export logů
    def on_save_log(self, sender):
        try:
            name=f"CyberDrill_{now_stamp()}.txt"
            path=os.path.join(os.path.expanduser('~/Documents'), name)
            with open(path,'w',encoding='utf-8') as f:
                f.write(self.console.text)
            self._println(f"[SYS] Log uložen: {name}")
        except Exception as e:
            self._println("[ERR] save: " + err_str(e))

    def on_copy_log(self, sender):
        try:
            clipboard.set(self.console.text or "")
            self._println("[SYS] Log zkopírován do schránky.")
        except Exception as e:
            self._println("[ERR] copy: " + err_str(e))

    # Selftest
    def on_selftest(self, sender):
        if not self.state:
            self._println("[SYS] Nejprve zvol misi a stiskni Start."); return
        self.state.run_selftest()

    # Net panel
    def on_net_host(self, sender):
        if not self.state: self._println("[NET] Nejprve Start."); return
        port=as_int(self.port_tf.text or "50555",50555); code=self.code_tf.text or ""
        ok,msg=self.state.net.host(port=port, code=code); self._println("[NET] "+msg); self._update_net_ui()
    def on_net_join(self, sender):
        if not self.state: self._println("[NET] Nejprve Start."); return
        host=self.ip_tf.text or ""; port=as_int(self.port_tf.text or "50555",50555); code=self.code_tf.text or ""
        ok,msg=self.state.net.join(host=host, port=port, code=code); self._println("[NET] "+msg)
        if ok: self.state.net.request_sync(); self._update_net_ui()
    def on_net_leave(self, sender):
        if not self.state: return
        self.state.net.leave(); self._println("[NET] Odpojeno"); self._update_net_ui()

    def _update_net_ui(self):
        if not self.state:
            self.peers_lbl.text="Peers: -"; return
        role = "HOST" if self.state.net.is_host() else ("CLIENT" if self.state.net.is_client() else "-")
        peers = ", ".join(self.state.net.list_peers()) or "-"
        self.peers_lbl.text=f"Role: {role}   Peers: {peers}"

    def _populate_quick(self, code):
        presets = {
            None: ["status","help","selftest","clear","map"],
            "A1": ["scan --passive","inject --target N2","status","log","selftest"],
            "D1": ["scan","mitigate --mode rate-limit --limit 100","status","log","selftest"],
            "A3": ["ddos --check","ddos --rate 5000 --target base-ops","mitigate --mode rate-limit --limit 120","ddos --stop","selftest"],
            "D3": ["arp --list","counter --target 192.168.0.5","arp --verify","status","selftest"],
            "P1": ["mail --inbox","mail --flag M-1337","mail --block secure-update.test","status","selftest"],
            "R1": ["fs --monitor","isolate --node node-2","restore --snapshot pre-incident","status","selftest"],
            "S1": ["repo --update repoX","verify --sig repoX","repo --quarantine repoX","status","selftest"],
            "I1": ["audit --list","account --disable bob","status","log","selftest"],
        }
        arr = presets.get(code, presets[None])
        for i,b in enumerate(self.quick):
            b.title = arr[i] if i<len(arr) else ""

# ========== run ==========
if __name__ == '__main__':
    try:
        v.close()
    except Exception:
        pass
    v = App()
    v.present('fullscreen')
