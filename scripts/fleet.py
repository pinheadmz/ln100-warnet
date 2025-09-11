import json
import os
import secrets
import sys
import yaml
from pathlib import Path
from random import randbytes, choice
from base64 import b64encode
from subprocess import run

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from test_framework.key import ECKey  # noqa: E402
from test_framework.script_util import key_to_p2wpkh_script  # noqa: E402
from test_framework.wallet_util import bytes_to_wif  # noqa: E402
from test_framework.descriptors import descsum_create  # noqa: E402

TEAMS = [
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces"
]

class Node:
    def __init__(self, game, name):
        self.game = game
        self.name = name
        self.bitcoin_image = {"tag": "29.0"}
        self.rpcpassword = secrets.token_hex(16)

        self.addnode = []

        self.lnd_image = {"tag": "v0.19.0-beta"}

        self.root_key_base64 = None
        self.admin_macaroon = None
        self.generate_macaroon()

        self.channels = []

    def generate_macaroon(self):
        entropy = randbytes(32)
        key_hex = entropy.hex()
        key_b64 = b64encode(entropy).decode()
        response = run(
            [
                "lncli",
                "bakemacaroon",
                f"--root_key={key_hex}",
                "address:read",
                "address:write",
                "info:read",
                "info:write",
                "invoices:read",
                "invoices:write",
                "macaroon:generate",
                "macaroon:read",
                "macaroon:write",
                "message:read",
                "message:write",
                "offchain:read",
                "offchain:write",
                "onchain:read",
                "onchain:write",
                "peers:read",
                "peers:write",
                "signer:generate",
                "signer:read"
            ],
            capture_output=True
        )
        self.root_key_base64 = key_b64
        self.admin_macaroon = response.stdout.decode().strip()

    def to_obj(self):
        return {
            "name": self.name,
            "image": self.bitcoin_image,
            "global": {
                "rpcpassword": self.rpcpassword,
                "chain": "signet"
            },
            "config":
                f'maxconnections=1000\n' +
                f'uacomment=miner{self.name}\n' +
                f'signetchallenge={self.game.signetchallenge}\n' +
                'coinstatsindex=1',
            "addnode": self.addnode,
            "ln": {"lnd": True},
            "lnd": {
                "image": self.lnd_image,
                "channels": self.channels,
                "config": f"alias={self.name}",
                "macaroonRootKey": self.root_key_base64,
                "adminMacaroon": self.admin_macaroon,
                "metricsExport": True,
                "prometheusMetricsPort": 9332,
                "extraContainers": [
                    {
                        "name": "lnd-exporter",
                        "image": "bitdonkey/lnd-exporter:0.1.3",
                        "imagePullPolicy": "IfNotPresent",
                        "volumeMounts": [
                            {
                                "name": "config",
                                "mountPath": "/macaroon.hex",
                                "subPath": "MACAROON_HEX"
                            }
                        ],
                        "env": [
                            {
                                "name": "METRICS",
                                "value":
                                    'lnd_balance_channels=parse("/v1/balance/channels","balance") ' +
                                    'lnd_local_balance_channels=parse("/v1/balance/channels","local_balance.sat") ' +
                                    'lnd_remote_balance_channels=parse("/v1/balance/channels","remote_balance.sat") ' +
                                    'lnd_block_height=parse("/v1/getinfo","block_height") ' +
                                    'lnd_peers=parse("/v1/getinfo","num_peers")'
                            }
                        ],
                        "ports": [
                            {
                                "name": "prom-metrics",
                                "containerPort": 9332,
                                "protocol": "TCP",
                            }
                        ]
                    }
                ]
            }
        }

class Miner(Node):
    def __init__(self, game):
        super().__init__(game, "miner")
        self.bitcoin_image = {"tag": "29.0-util"}

    def to_obj(self):
        obj = super().to_obj()
        obj.pop("lnd")
        obj.pop("ln")
        obj.update({
            "startupProbe": {
                "failureThreshold": 10,
                "periodSeconds": 30,
                "successThreshold": 1,
                "timeoutSeconds": 60,
                "exec": {
                    "command": [
                        "/bin/sh",
                        "-c",
                        "bitcoin-cli createwallet miner && " +
                        f"bitcoin-cli importdescriptors {self.game.desc_string}"
                    ]
                }
            }
        })
        return obj

class Game:
    def __init__(self, network_name):
        self.network_name = network_name
        self.signetchallenge = None
        self.desc_string = None
        self.generate_signet()

        self.nodes = []

    def generate_signet(self):
        # generate entropy
        secret = secrets.token_bytes(32)

        # derive private key and set global signet challenge (simple p2wpkh script)
        privkey = ECKey()
        privkey.set(secret, True)
        pubkey = privkey.get_pubkey().get_bytes()
        challenge = key_to_p2wpkh_script(pubkey)
        self.signetchallenge = challenge.hex()

        # output a bash script that executes warnet commands creating
        # a wallet on the miner node that can create signet blocks
        privkeywif=bytes_to_wif(secret)
        desc = descsum_create('combo(' + privkeywif + ')')
        desc_import = [{
            'desc': desc,
            'timestamp': 0
        }]
        desc_string = json.dumps(desc_import)
        self.desc_string = desc_string.replace("\"", "\\\"").replace(" ", "").replace("(", "\\(").replace(")", "\\)").replace(",", "\\,")

    def add_nodes(self, num_nodes):
        for n in range(num_nodes):
            self.nodes.append(Node(self, f"tank-{n:04d}"))

    def add_channels(self, n):
        # random for now
        block = 500
        index = 1
        for _ in range(1, n):
            src = choice(self.nodes)
            tgt = choice(self.nodes)
            if src == tgt:
                continue
            src.channels.append({
                "id": {"block": block, "index": index},
                "target": f"{tgt.name}-ln",
                "capacity": 300000,
                "push_amt": 150000
            })
            index += 1
            if index > 200:
                index = 1
                block += 1

    def add_miner(self):
        miner = Miner(self)
        for node in self.nodes:
            node.addnode.append("miner")
        # do this last, don't connect to self
        self.nodes.append(miner)

    def write(self):
        network = {
            "nodes": [n.to_obj() for n in self.nodes],
            "caddy": {"enabled": True}
        }
        try:
            os.mkdir(Path(os.path.dirname(__file__)) / ".." / "networks" / self.network_name, exist_ok=True)
        except:
            pass
        with open(Path(os.path.dirname(__file__)) / ".." / "networks" / self.network_name / "network.yaml", "w") as f:
            yaml.dump(network, f, default_flow_style=False)
        with open(Path(os.path.dirname(__file__)) / ".." / "networks" / self.network_name / "node-defaults.yaml", "w") as f:
            f.write("comment: enjoy")


game = Game("test")
game.add_nodes(100)
game.add_channels(500)
game.add_miner()
game.write()



# def custom_graph(
#     teams: int,
#     num_connections: int,
#     version: str,
#     datadir: Path,
#     fork_observer: bool,
#     fork_obs_query_interval: int,
#     caddy: bool,
#     logging: bool,
#     signetchallenge: str
# ):
#     try:
#         datadir.mkdir(parents=False, exist_ok=True)
#     except FileExistsError as e:
#         print(e)
#         print("Exiting network builder without overwriting")
#         sys.exit(1)

#     # Generate network.yaml
#     nodes = []
#     connections = set()

#     extra = f"\nsignetchallenge={signetchallenge}" if signetchallenge else ""

#     # add central admin miner node
#     nodes.append({
#         "name": "miner",
#         "addnode": [],
#         "image": {"tag": "29.0"},
#         "global": {"rpcpassword": secrets.token_hex(16)},
#         "config": f"maxconnections=1000\nuacomment=miner{extra}\ncoinstatsindex=1",
#         "metrics": "txrate=getchaintxstats(10)[\"txrate\"] utxosetsize=gettxoutsetinfo()[\"txouts\"]"
#     })
#     num_nodes = teams * len(VERSIONS)
#     for i in range(num_nodes):
#         team = TEAMS[i // len(VERSIONS)]
#         name = f"tank-{i:04d}-{team}"
#         nodes.append({
#             "name": name,
#             "addnode": [],
#             "image": {"tag": next(version_generator)},
#             "global": {"rpcpassword": secrets.token_hex(16)},
#             "config": f"uacomment={team}{extra}"
#         })

#     for i, node in enumerate(nodes):
#         # Add round-robin connection
#         next_node_index = (i + 1) % num_nodes
#         node["addnode"].append(nodes[next_node_index]["name"])
#         connections.add((i, next_node_index))

#         # Add random connections including miner
#         available_nodes = list(range(num_nodes + 1))
#         available_nodes.remove(i)
#         if next_node_index in available_nodes:
#             available_nodes.remove(next_node_index)

#         for _ in range(min(num_connections - 1, len(available_nodes))):
#             random_node_index = random.choice(available_nodes)
#             # Avoid circular loops of A -> B -> A
#             if (random_node_index, i) not in connections:
#                 node["addnode"].append(nodes[random_node_index]["name"])
#                 connections.add((i, random_node_index))
#                 available_nodes.remove(random_node_index)


#     network_yaml_data = {"nodes": nodes}
#     network_yaml_data["fork_observer"] = {
#         "enabled": fork_observer,
#         "configQueryInterval": fork_obs_query_interval,
#     }
#     network_yaml_data["caddy"] = {
#         "enabled": caddy,
#     }

#     with open(os.path.join(datadir, "network.yaml"), "w") as f:
#         yaml.dump(network_yaml_data, f, default_flow_style=False)


# custom_graph(
#     teams=len(TEAMS),
#     num_connections=8,
#     version="27.0",
#     datadir=Path(os.path.dirname(__file__)).parent.parent / "networks" / "admin" / "battlefield",
#     fork_observer=True,
#     fork_obs_query_interval=20,
#     caddy=True,
#     logging=True,
#     signetchallenge=signetchallenge)


# custom_graph(
#     teams=1,
#     num_connections=8,
#     version="27.0",
#     datadir=Path(os.path.dirname(__file__)).parent.parent / "networks" / "scrimmage",
#     fork_observer=True,
#     fork_obs_query_interval=20,
#     caddy=True,
#     logging=True,
#     signetchallenge="51")


# custom_graph(
#     teams=1,
#     num_connections=8,
#     version="27.0",
#     datadir=Path(os.path.dirname(__file__)).parent.parent / "networks" / "admin" / "scrimmage_nologging",
#     fork_observer=False,
#     fork_obs_query_interval=20,
#     caddy=False,
#     logging=False,
#     signetchallenge="51")

# armies = {"namespaces": []}
# for team in TEAMS:
#     armies["namespaces"].append({"name": f"wargames-{team}"})
# with open(Path(os.path.dirname(__file__)).parent / "namespaces" / "armies" / "namespaces.yaml", "w") as f:
#     yaml.dump(armies, f, default_flow_style=False)

# armadanet = {
#     "nodes": [
#         {"name": "armada-0", "config": f"signetchallenge={signetchallenge}"},
#         {"name": "armada-1", "config": f"signetchallenge={signetchallenge}"},
#         {"name": "armada-2", "config": f"signetchallenge={signetchallenge}"}
#     ]
# }
# with open(Path(os.path.dirname(__file__)).parent.parent / "networks" / "armada" / "network.yaml", "w") as f:
#     yaml.dump(armadanet, f, default_flow_style=False)

