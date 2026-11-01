"""Editable catalog whose public product IDs never depend on storage record IDs."""
import re
from app.domain.identifiers import new_id

from app.domain.errors import DomainError


def product_names(product):
    return list(dict.fromkeys(v.strip() for v in [product['id'], product.get('name', ''), *product.get('aliases', [])] if isinstance(v, str) and v.strip()))


def response_product_name(product):
    """Only registered non-Chinese display names enter new formal English responses."""
    return next((name.strip() for name in [product.get('name',''), *product.get('aliases',[])]
                 if name.strip() and not re.search(r'[\u4e00-\u9fff]', name)), 'The product')


class ProductCatalogService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def products(self):
        known = {p['id']: {**p, 'family':p.get('family',''), 'version': 0, 'origin': 'builtin'} for p in self.settings.products}
        for record in self.repo.list('kb_product', limit=100000):
            ident = record['product_id']
            known[ident] = {'id': ident, 'name': record['name'], 'aliases': record.get('aliases', []), 'family':record.get('family',''),
                            'version': record['version'], 'origin': 'builtin' if ident in {p['id'] for p in self.settings.products} else 'custom'}
        return list(known.values())

    def resolve_product(self, name):
        value = name.strip().casefold() if isinstance(name, str) else ''
        for product in self.products():
            if value in {v.casefold() for v in product_names(product)}:
                return product['id']
        raise DomainError('PRODUCT_REQUIRED', '请选择已登记产品型号', 422)

    def _validated(self, ident, name, aliases, family=''):
        if not isinstance(family,str) or len(family.strip())>100:
            raise DomainError('PRODUCT_INVALID','产品家族格式不正确',422)
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,79}', ident) or not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() or len(a.strip()) > 100 for a in aliases):
            raise DomainError('PRODUCT_INVALID', '型号标识、名称或别名格式不正确', 422)
        clean = []
        for alias in aliases:
            if alias.strip().casefold() not in {a.casefold() for a in clean}:
                clean.append(alias.strip())
        return {'id': ident, 'name': name.strip(), 'aliases': clean, 'family':family.strip()}

    def _check_collisions(self, candidate, exclude=None):
        existing = {value.casefold() for product in self.products() if product['id'] != exclude for value in product_names(product)}
        if any(value.casefold() in existing for value in product_names(candidate)):
            raise DomainError('PRODUCT_EXISTS', '产品型号、名称或别名已存在', 409)

    def create_product(self, body):
        if not body.get('id') and (not isinstance(body.get('name'),str) or not body['name'].strip()):
            raise DomainError('PRODUCT_INVALID','新建产品需要有效产品名称',422)
        raw = body.get('id') or ('product-'+new_id())
        ident = raw.strip() if isinstance(raw, str) else raw
        candidate = self._validated(ident, body.get('name') or ident, body.get('aliases', []), body.get('family',''))
        with self.repo.transaction():
            self._check_collisions(candidate)
            self.repo.put('kb_product', {'product_id': ident, 'name': candidate['name'], 'aliases': candidate['aliases'], 'family':candidate['family']})
            return next(p for p in self.products() if p['id'] == ident)

    def update_product(self, ident, body):
        if set(body) - {'expected_version', 'name', 'aliases', 'family'} or type(body.get('expected_version')) is not int:
            raise DomainError('PRODUCT_INVALID', '编辑产品需要版本号、名称和别名，型号标识不可更改', 422)
        with self.repo.transaction():
            product = next((p for p in self.products() if p['id'] == ident), None)
            if not product:
                raise DomainError('NOT_FOUND', '产品不存在', 404)
            if body['expected_version'] != product['version']:
                raise DomainError('VERSION_CONFLICT', '产品已更新，请刷新后重新操作', 409)
            candidate = self._validated(ident, body.get('name', product['name']), body.get('aliases', product.get('aliases', [])), body.get('family',product.get('family','')))
            self._check_collisions(candidate, exclude=ident)
            changes = {'product_id': ident, 'name': candidate['name'], 'aliases': candidate['aliases'], 'family':candidate['family']}
            record = next((p for p in self.repo.list('kb_product', limit=100000) if p['product_id'] == ident), None)
            if record:
                self.repo.update('kb_product', record['id'], changes, body['expected_version'])
            else:
                self.repo.put('kb_product', changes)
            self.repo.audit(None, 'product.update', {'product_id': ident, 'previous_version': product['version']})
            return next(p for p in self.products() if p['id'] == ident)
