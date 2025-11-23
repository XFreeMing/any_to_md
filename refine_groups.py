
subcategories = {
    "Cognitive & Analysis (认知与分析)": {
        "Thinking & Understanding (思考与理解)": [
            "conceive", "envision", "realize", "interpret", "decipher", "deduce", "discern", "distill", 
            "illuminate", "clarify", "characterize", "identify", "generalize", "hypothesize", "postulate", 
            "predetermine", "denote", "objectify", "qualify", "bracket", "subsume"
        ],
        "Examining & Checking (检查与观察)": [
            "analyze", "investigate", "monitor", "observe", "peruse"
        ],
        "Judging & Deciding (判断与决定)": [
            "adjudicate", "conclude", "resolve", "reconcile"
        ],
        "Validating & Confirming (验证与确认)": [
            "validate", "confirm", "certify", "attest", "substantiate", "warrant", "endorse", "sanction", 
            "permit", "consent", "subscribe", "document"
        ],
        "Organizing & Classifying (组织与分类)": [
            "classify", "categorize", "catalogue", "index", "label", "organize", "sequence", "outline", 
            "summarize", "synthesize"
        ]
    },
    "Communication & Interaction (沟通与互动)": {
        "Speaking & Expressing (表达与宣称)": [
            "articulate", "express", "proclaim", "argue", "contend", "advocate", "propose", "submit"
        ],
        "Discussing & Consulting (讨论与咨询)": [
            "confer", "consult", "converse", "negotiate", "convene"
        ],
        "Influencing & Guiding (影响与指导)": [
            "influence", "lobby", "instruct", "inform", "inculcate", "prescribe", "impose", "mobilize", 
            "recruit", "invite", "solicit", "elicit"
        ],
        "Connecting & Collaborating (连接与合作)": [
            "collaborate", "coordinate", "network", "interact", "interconnect", "synergize", "associate", 
            "relate", "engage", "involve", "participate", "encounter", "enjoy"
        ],
        "Giving & Providing (给予与提供)": [
            "provide", "furnish", "equip", "bestow", "lend", "concede"
        ]
    },
    "Creation & Development (创造与发展)": {
        "Creating & Inventing (创造与发明)": [
            "invent", "devise", "forge", "formulate", "nucleate"
        ],
        "Building & Constructing (构建与建设)": [
            "construct", "establish", "institute", "compile"
        ],
        "Starting & Initiating (开始与启动)": [
            "initiate", "launch", "invoke", "emerge"
        ],
        "Developing & Growing (发展与培养)": [
            "develop", "cultivate", "crystalize", "elaborate", "derive"
        ]
    },
    "Modification & Change (修改与变化)": {
        "Changing & Altering (改变与更改)": [
            "vary", "fluctuate", "deviate", "diverge", "masquerade"
        ],
        "Improving & Refining (改进与优化)": [
            "improve", "refine", "streamline", "simplify", "moderate", "modulate"
        ],
        "Adapting & Adjusting (适应与调整)": [
            "adapt", "customize", "configure"
        ],
        "Transforming & Converting (转换与变形)": [
            "convert", "invert", "render", "reform", "rearrange", "compound", "shape", "sharp", "vulcanize"
        ]
    },
    "Expansion & Increase (扩展与增加)": {
        "Increasing & Growing (增加与增长)": [
            "boost", "augment", "amplify", "multiply", "proliferate", "escalate", "exacerbate", "culminate"
        ],
        "Expanding & Widening (扩展与拓宽)": [
            "extend", "broaden", "widen", "enlarge", "propagate"
        ],
        "Strengthening & Enhancing (加强与提升)": [
            "strengthen", "enhance", "intensify", "heighten", "elevate", "invigorate", "consolidate", 
            "promote", "advance", "progress", "further", "foster", "sustain", "suffice", "supplement", 
            "accentuate"
        ]
    },
    "Reduction & Decrease (减少与降低)": {
        "Decreasing & Lessening (减少与减轻)": [
            "diminish", "attenuate", "recede", "contract", "condense", "miniaturize"
        ],
        "Removing & Separating (移除与分离)": [
            "exclude", "extract", "purge", "sever", "decouple", "disengage", "isolate", "confine", 
            "constrain", "encapsulate"
        ],
        "Stopping & Disrupting (停止与破坏)": [
            "disrupt", "undermine", "negate", "deactivate", "dissolve", "decompose", "erode", "perturb", 
            "evade", "divert", "displace", "disperse"
        ]
    },
    "Action & Execution (行动与执行)": {
        "Doing & Performing (做与执行)": [
            "perform", "execute", "enact", "conduct", "implement"
        ],
        "Using & Employing (使用与利用)": [
            "employ", "apply", "exploit", "leverage", "avail", "operate", "manipulate", "handle"
        ],
        "Managing & Controlling (管理与控制)": [
            "manage", "preside", "oversee", "maintain", "process", "function"
        ],
        "Getting & Acquiring (获取与获得)": [
            "acquire", "attain", "accrue", "take", "undertake", "adopt", "install", "activate", "fuel", 
            "facilitate"
        ]
    },
    "Movement & Positioning (移动与定位)": {
        "Moving & Transferring (移动与转移)": [
            "convey", "import", "export", "diffuse", "disseminate"
        ],
        "Placing & Putting (放置与安置)": [
            "allocate", "appropriate", "embed", "immerse", "infuse", "inoculate", "include", "integrate", 
            "combine", "adjunct"
        ],
        "Spreading & Covering (传播与覆盖)": [
            "pervade", "intrude", "encroach", "outreach", "map", "delineate", "carve", "atone"
        ]
    },
    "State & Relation (状态与关系)": {
        "State & Relation (状态与关系)": [
            "affect", "assume", "endeavor", "equalize", "impact", "implicate", "pertain", "predispose", 
            "sustain"
        ]
    }
}

print("# 500 Verbs Grouped & Refined\n")

for category, subs in subcategories.items():
    print(f"## {category}")
    for sub, words in subs.items():
        if words:
            print(f"### {sub}")
            # Print in chunks of 10
            for i in range(0, len(words), 10):
                print(", ".join(words[i:i+10]))
            print()
