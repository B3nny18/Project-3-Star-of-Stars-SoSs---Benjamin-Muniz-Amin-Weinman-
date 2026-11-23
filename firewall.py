import os

from protocol import addr_net, parse_addr


class FirewallRules:
    def __init__(self):
        self.blocked_networks = set()  # X_# rules
        self.blocked_nodes = set()     # X_Y rules (address bytes)
        self.rules = []                # rule targets as text, in file order ("3_5", "2_#")

    @classmethod
    def load(cls, path):
        rules = cls()
        if path is None or not os.path.isfile(path):
            return rules  # no file: nothing is blocked
        with open(path, "r", encoding="ascii") as f:
            for line in f:
                target, _, action = line.strip().partition(":")
                if target and action.strip().lower() == "local":
                    rules.add(target.strip())
        return rules

    def add(self, target):
        if target in self.rules:
            return
        net, _, node = target.partition("_")
        if node == "#":
            self.blocked_networks.add(int(net))
        else:
            self.blocked_nodes.add(parse_addr(target))
        self.rules.append(target)

    def rules_for_network(self, net):
        return [r for r in self.rules if r.partition("_")[0] == str(net)]

    def network_blocked(self, net):
        return net in self.blocked_networks

    def node_blocked(self, addr):
        return addr in self.blocked_nodes

    def blocks_global(self, frame):
        return (self.network_blocked(addr_net(frame.src))
                or self.network_blocked(addr_net(frame.dst)))

    def __repr__(self):
        return ", ".join(f"{r}: local" for r in self.rules) or "(no rules)"
