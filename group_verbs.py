
import re

content = """
identify, extend, characterize, outreach, catalogue, map, amplify, simplify, vary, moderate, encapsulate, exclude, sequence, isolate, fluctuate, delineate, carve, confine, outreach, encroach, investigate, masquerade
classify, index, combine, import, categorize, broaden, invent, export, refine, compile, express, confer, summarize, bracket, generalize, subsume, label, render, organize, isolate, sequence, appropriate, apply, acquire, inform, adopt, instruct, solicit, instruct, isolate, nucleate, monitor, vulcanize, nucleate, fluctuate, deviate, argue, improve
integrate, import, invert, render, convert, crystalize, appropriate, confirm, proclaim, inculcate, adapt, cultivate, isolate, solicit, nucleate, manipulate, mobilize, modulate
refine, interpret, allocate, synthesize, condense, clarify, deduce, reform, rearrange, outline, compound, simplify, diffuse, lobby, cultivate, adapt, inoculate, diffuse, observe, emerge, devise, derive, diverge
decouple, simplify, deactivate, decouple, recede, refine, negate, condense, decompose, streamline, recede, allocate, erode, disrupt, divert, displace, diminish, sever, dissolve, emerge, evade, disperse, isolate, miniaturize, disengage, purge
amplify, boost, augment, invigorate, consolidate, lengthen, enhance, decipher, heighten, broaden, further, intensify, enrich, escalate, modulate, exacerbate, propagate, perturb, culminate, diffuse, infuse, stimulate, exude, attenuate, proliferate, maximize, elevate, disseminate, infuse, elaborate, maximize, develop, enlarge, attenuate, construct, invigorate, multiply, suffice, elaborate, formulate, distill, customize, strengthen, equip, supplement, cultivate, sustain, progress, advance, mobilize, inculcate, foster
activate, establish, install, launch, invoke, devise, construct, contract, institute, undertake, establish, forge, develop, customize, strengthen, immerse, envision, realize, undertake, employ, implement, furnish, provide, submit, sustain, augment, cultivate, solicit, bestow
affect, configure, influence, relate, embed, associate, impact, mediate, interact, contend, undermine, negotiate, resolve, relate, implicate, confine, contract, perturb, leverage, involve, equalize, immerse, constrain, disrupt, engage, affect, shape, differentiate, take, avail, exploit, impact, sharp, fuel, facilitate, network, coordinate, collaborate, articulate, interact, interconnect, synergize, communicate, coordinate, instruct, network, encounter
substantiate, subscribe, denote, attest, predetermine, hypothesize, postulate, analyze, embed, qualify, embed, certify, validate, attest, document, substantiate, atone, attest, adjunct, conclude, include
mobilize, invite, engage, enact, employ, execute, prescribe, involve, concede, reconcile, conduct, resolve, enact, invite, elicit, discern, illuminate, devise, infuse, convey, objectify, validate, elaborate, intrude, document, adjudicate, attest, enact, impose, adjunct, include
operate, manage, preside, function, maintain, oversee, refine, consult, inform, perform, maintain, reconcile, convene, acquire, peruse, initiate, pertain, converse, assume, endeavor, inform, propose, conceive, widen, buxtons, promote, operate, participate, enjoy, solicit, lend, warrant, sanction, permit, advocate, predispose, accentuate
process, endorse, involve, employ, perform, maintain, preside, consent, recruit, extract, validate, confirm, oversee, attain, accrue, pervade, manage, elicit, resolve, convene, acquire, peruse, initiate, pertain, converse, assume, endeavor, inform, propose, conceive, widen, buxtons, promote, operate, participate, enjoy, solicit, lend, warrant, sanction, permit, advocate, predispose, accentuate
"""

# Normalize and deduplicate
words = [w.strip() for w in content.replace('\n', ',').split(',') if w.strip()]
unique_words = sorted(list(set(words)))

categories = {
    "Cognitive & Analysis (认知与分析)": [
        "identify", "characterize", "investigate", "classify", "index", "categorize", "summarize", "generalize", 
        "subsume", "label", "organize", "sequence", "monitor", "interpret", "synthesize", "clarify", "deduce", 
        "outline", "decipher", "distill", "envision", "realize", "differentiate", "analyze", "hypothesize", 
        "postulate", "qualify", "discern", "illuminate", "objectify", "peruse", "conceive", "predetermine", 
        "denote", "subscribe", "substantiate", "attest", "document", "certify", "validate", "confirm", "resolve", 
        "reconcile", "adjudicate", "conclude", "assess", "evaluate", "measure", "check", "test", "verify", "catalogue",
        "bracket", "observe", "warrant", "sanction", "permit", "endorse", "consent", "approve"
    ],
    "Communication & Interaction (沟通与互动)": [
        "express", "confer", "inform", "instruct", "solicit", "argue", "proclaim", "inculcate", "lobby", 
        "negotiate", "consult", "converse", "propose", "advocate", "articulate", "communicate", "contact", 
        "interact", "relate", "associate", "mediate", "contend", "network", "coordinate", "collaborate", 
        "interconnect", "synergize", "encounter", "invite", "engage", "involve", "recruit", "mobilize", 
        "influence", "persuade", "convince", "elicit", "concede", "prescribe", "impose", "participate", "enjoy",
        "lend", "bestow", "submit", "provide", "furnish", "equip"
    ],
    "Creation & Development (创造与发展)": [
        "invent", "compile", "nucleate", "crystalize", "cultivate", "devise", "derive", "emerge", "formulate", 
        "construct", "develop", "forge", "establish", "institute", "launch", "invoke", "initiate", "generate", 
        "produce", "build", "create", "compose", "design", "engineer", "fabricate", "manufacture", "assemble",
        "elaborate"
    ],
    "Modification & Change (修改与变化)": [
        "simplify", "vary", "moderate", "refine", "adapt", "modulate", "reform", "rearrange", "compound", 
        "streamline", "customize", "configure", "shape", "sharp", "adjust", "alter", "modify", "convert", 
        "invert", "render", "transform", "vulcanize", "fluctuate", "deviate", "diverge", "evolve", "improve", 
        "upgrade", "update", "renew", "restore", "repair", "fix", "mend", "masquerade"
    ],
    "Expansion & Increase (扩展与增加)": [
        "extend", "amplify", "broaden", "boost", "augment", "invigorate", "consolidate", "lengthen", "enhance", 
        "heighten", "further", "intensify", "enrich", "escalate", "exacerbate", "propagate", "stimulate", 
        "proliferate", "maximize", "elevate", "enlarge", "multiply", "strengthen", "supplement", "progress", 
        "advance", "foster", "widen", "accentuate", "promote", "expand", "increase", "grow", "raise", "rise", 
        "climb", "surge", "soar", "culminate", "suffice"
    ],
    "Reduction & Decrease (减少与降低)": [
        "encapsulate", "exclude", "isolate", "confine", "decouple", "deactivate", "recede", "negate", "condense", 
        "decompose", "erode", "disrupt", "divert", "displace", "diminish", "sever", "dissolve", "evade", 
        "disperse", "miniaturize", "disengage", "purge", "attenuate", "distill", "extract", "contract", 
        "reduce", "decrease", "lower", "drop", "fall", "decline", "shrink", "shorten", "lessen", "mitigate", 
        "alleviate", "undermine", "perturb"
    ],
    "Action & Execution (行动与执行)": [
        "apply", "acquire", "adopt", "manipulate", "operate", "manage", "preside", "function", "maintain", 
        "oversee", "perform", "implement", "execute", "conduct", "process", "employ", "utilize", "exploit", 
        "leverage", "take", "avail", "fuel", "facilitate", "activate", "install", "undertake", "use", "handle", 
        "control", "direct", "guide", "lead", "drive", "attain", "accrue"
    ],
    "Movement & Positioning (移动与定位)": [
        "map", "delineate", "carve", "encroach", "outreach", "import", "export", "appropriate", "allocate", 
        "diffuse", "inoculate", "disseminate", "infuse", "exude", "embed", "immerse", "pervade", "intrude", 
        "circulate", "distribute", "spread", "move", "shift", "transfer", "transport", "transmit", "convey", 
        "send", "receive", "bring", "fetch", "carry", "lift", "push", "pull", "drag", "throw", "cast",
        "combine", "integrate", "include", "adjunct", "atone"
    ],
    "State & Relation (状态与关系)": [
        "sustain", "affect", "impact", "implicate", "equalize", "pertain", "assume", "endeavor", 
        "predispose", "belong", "exist", "live", "die", "stand", "sit", "lie", "hang", "fall", "remain", 
        "stay", "wait", "expect", "hope", "wish", "want", "need", "love", "hate", "like", "dislike", "fear", "dread"
    ]
}

categorized_words = {k: [] for k in categories}
uncategorized = []

for word in unique_words:
    found = False
    for cat, cat_words in categories.items():
        if word in cat_words:
            categorized_words[cat].append(word)
            found = True
            break
    if not found:
        uncategorized.append(word)

print("# 500 Verbs Grouped\n")
for cat, words in categorized_words.items():
    if words:
        print(f"## {cat}")
        # Print in chunks of 10 for readability
        for i in range(0, len(words), 10):
            print(", ".join(words[i:i+10]))
        print()

if uncategorized:
    print("## Uncategorized (未分类)")
    for i in range(0, len(uncategorized), 10):
        print(", ".join(uncategorized[i:i+10]))
    print()
