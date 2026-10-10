"""Versioned physical Quest/Talk mappings. No native-ID identity or guessed keys.

Each mapping is local to an explicitly classified source shape. Unmapped fields
remain in the raw JSON. The legacy v1-v3 parser never imports this module.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

CONTRACT = 'quest-talk-evidence-v4'
SHAPE_CONTRACT = 'quest-talk-physical-shapes-v1'
SNAPSHOT = 'anime-game-data:7.0:26df1dfbdf05a82bbb1d97506859f3e1c40718d8'
# root ID, dialogue list, node ID, successor list, content TextMap hash.
DIALOGUES = {
 'rc': ('IOKNFDJFGDH','PFALHAKIILD','OIFGMOHKPOI','KMLAFCBMFEI','OACNIBLFFDI'),
 'alt': ('AADKDKPMGNO','GALIDJOEHOC','NFIEHACCECI','GBLICFDCPCK','AIGJBMCHCJG'),
 'third': ('LDLMECNIJFC','GDDPNNHLGBL','ANKFNLMKOII','OMAHHDBCAPB','DMIFDJDEFAL'),
 'normalized': ('talkId','dialogList','id','nextDialogs','talkContentTextMapHash'),
 'older_a': ('FEOACBMDCKJ','AAOAAFLLOJI','CCFPGAKINNB','FNNPCGIAELE','BDOKCLNNDGN'),
 'older_b': ('PBAEPDPNKEJ','KJNKFMPAGAA','JOLEJEFDNJJ','CLMNEDLMAJL','EMKCOIBADBJ'),
 'older_c': ('ADHLLDAPKCM','MOEOFGCKILF','ILHDNJDDEOP','OCPFKNFFMMF','GABLFFECBDO'),
 'older_d': ('LBPGKDMGFBN','LOJEOMAPIIM','BLKKAMEMBBJ','FLCJOHFLGND','CMKPOJOEHHA'),
}
QUESTS = {'rc': ('OIFGMOHKPOI','OJACLOOEAMG','EBNBLBEIFFJ'),
          'alt': ('NFIEHACCECI','NFFIGDHFAJG','MEGJPCLADOG')}
REFERENCES = {'rc': ('OIFGMOHKPOI','MCIAGDCECPL'),
              'alt': ('NFIEHACCECI','BKHBKMLIPLN'),
              'normalized': ('id','initDialog'),
              'older_a': ('CCFPGAKINNB',None),
              'older_b': ('JOLEJEFDNJJ','FBALOFKGJKN'),
              'blossom': ('ANKFNLMKOII','GIIPBNJFFAK')}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f'{label}: expected nonnegative native integer')
    return value


def read(path):
    return json.loads(Path(path).read_bytes().decode('utf-8-sig'))


def classify(doc, shapes):
    signature = tuple(sorted(doc))
    found = [s for s in shapes if tuple(s['root_keys']) == signature]
    if len(found) != 1:
        raise ValueError(f'unregistered physical root signature: {signature}')
    return found[0]


def nodes(doc, shape, *, embedded=False):
    _, field, node_key, next_key, text_key = DIALOGUES[shape]
    value = doc if embedded else doc.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f'{field} must be a physical list')
    result=[]
    for ordinal, node in enumerate(value):
        if not isinstance(node, dict):
            raise ValueError('dialogue node must be object')
        native = integer(node[node_key], node_key)
        successors = node.get(next_key, [])
        if not isinstance(successors, list):raise ValueError('successors must be a list')
        for target in successors:integer(target, next_key)
        locator = node.get(text_key)
        if locator is not None:integer(locator, text_key)
        result.append({'ordinal': ordinal, 'pointer': f'/{field}/{ordinal}',
                       'native_id': native, 'next': successors, 'text_hash': locator,
                       'raw': node, 'text_mapping': 'unresolved_codec' if shape == 'alt' else 'literal_key'})
    return result


def references(doc, field, shape):
    value = [doc] if field is None else doc[field]
    if not isinstance(value, list):raise ValueError('reference wrapper must be a list')
    key, init = REFERENCES[shape]
    result=[]
    for i, item in enumerate(value):
        if not isinstance(item, dict):raise ValueError('reference must be an object')
        native = integer(item[key], key)
        if init is not None and init in item:integer(item[init], init)
        result.append({'pointer': '/' if field is None else f'/{field}/{i}',
                       'native_id': native, 'raw': item})
    return result


def quest(doc):
    # Disjoint required field groups, not a sequence of guessed aliases.
    matches=[shape for shape, keys in QUESTS.items() if all(k in doc for k in keys[1:])]
    if len(matches)!=1:raise ValueError('unknown or conflicting Quest physical shape')
    shape=matches[0];native,refs,children=QUESTS[shape]
    if native in doc:integer(doc[native],native)
    if not isinstance(doc[children],list):raise ValueError('Quest child records must be list')
    return shape,doc.get(native),references(doc,refs,shape)
