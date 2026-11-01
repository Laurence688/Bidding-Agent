from zipfile import ZipFile
from xml.etree import ElementTree as ET
from app.excel.package_patch import sheet_paths, NS
from app.domain.errors import DomainError


def verify_fidelity(source, output, values):
    with ZipFile(source) as left, ZipFile(output) as right:
        if left.namelist() != right.namelist():
            raise DomainError('FIDELITY_FAILED', '工作簿包结构发生变化', 409)
        paths = sheet_paths(left)
        targets = {paths[sheet]: cells for sheet, cells in values.items()}
        for name in left.namelist():
            if name not in targets:
                if left.read(name) != right.read(name):
                    raise DomainError('FIDELITY_FAILED', '非目标包内容变化', 409, {'part': name})
                continue
            a, b = ET.fromstring(left.read(name)), ET.fromstring(right.read(name))
            old = {c.attrib['r']: c for c in a.iter(f'{{{NS}}}c')}
            new = {c.attrib['r']: c for c in b.iter(f'{{{NS}}}c')}
            for coordinate, wanted in targets[name].items():
                c = new.get(coordinate)
                if c is None or c.attrib.get('t') != 'inlineStr' or ''.join(c.itertext()) != str(wanted):
                    raise DomainError('FIDELITY_FAILED', '目标回填值不一致', 409)
                if coordinate in old and {k: v for k, v in old[coordinate].attrib.items() if k != 't'} != {k: v for k, v in c.attrib.items() if k != 't'}:
                    raise DomainError('FIDELITY_FAILED', '目标单元格样式或属性变化', 409)
            for root in (a, b):
                for parent in root.iter():
                    for child in list(parent):
                        if child.tag == f'{{{NS}}}dimension' or (child.tag == f'{{{NS}}}c' and child.attrib.get('r') in targets[name]):
                            parent.remove(child)
            if ET.tostring(a) != ET.tostring(b):
                raise DomainError('FIDELITY_FAILED', '非目标单元格或工作表结构变化', 409)
    return True
