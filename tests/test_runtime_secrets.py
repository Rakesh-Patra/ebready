import pytest

from scripts.store_runtime_secrets import read_runtime_values, merge_secret_references


def test_role_lists_remain_distinct_and_aws_credentials_are_excluded(tmp_path):
    env = tmp_path / '.env'
    env.write_text('''EBKIT_ALLOWED_EMAILS=Evaluator@example.invalid,paid@example.invalid
EBKIT_TESTING_EMAILS=evaluator@example.invalid,paid@example.invalid
EBKIT_EVALUATOR_EMAILS=evaluator@example.invalid
EBKIT_PAID_TEST_EMAILS=paid@example.invalid
AWS_SECRET_ACCESS_KEY=synthetic-do-not-store
''')
    values = read_runtime_values(env)
    assert values['EBKIT_EVALUATOR_EMAILS'] == 'evaluator@example.invalid'
    assert values['EBKIT_PAID_TEST_EMAILS'] == 'paid@example.invalid'
    assert values['EBKIT_TESTING_EMAILS'] != values['EBKIT_EVALUATOR_EMAILS']
    assert 'AWS_SECRET_ACCESS_KEY' not in values


def test_empty_roles_are_preserved_for_revocation(tmp_path):
    env = tmp_path / '.env'
    env.write_text('EBKIT_ALLOWED_EMAILS=user@example.invalid\nEBKIT_TESTING_EMAILS=\nEBKIT_EVALUATOR_EMAILS=\nEBKIT_PAID_TEST_EMAILS=\n')
    assert read_runtime_values(env)['EBKIT_EVALUATOR_EMAILS'] == ''


@pytest.mark.parametrize('contents', [
    'EBKIT_ALLOWED_EMAILS=user@example.invalid\n',
    'EBKIT_ALLOWED_EMAILS=user@example.invalid\nEBKIT_TESTING_EMAILS=\nEBKIT_EVALUATOR_EMAILS=other@example.invalid\nEBKIT_PAID_TEST_EMAILS=\n',
])
def test_incomplete_or_unapproved_roles_fail_before_aws_calls(tmp_path, contents):
    env = tmp_path / '.env'
    env.write_text(contents)
    with pytest.raises(ValueError):
        read_runtime_values(env)


def test_partial_secret_update_preserves_other_references():
    original = {'cluster_live_enabled': False, 'secret_arns': {'INSFORGE_API_KEY': 'existing', 'EBKIT_EVALUATOR_EMAILS': 'old'}}
    result = merge_secret_references(original, {'EBKIT_EVALUATOR_EMAILS': 'new'})
    assert result['secret_arns'] == {'INSFORGE_API_KEY': 'existing', 'EBKIT_EVALUATOR_EMAILS': 'new'}
    assert original['secret_arns']['EBKIT_EVALUATOR_EMAILS'] == 'old'
    assert result['cluster_live_enabled'] is False
