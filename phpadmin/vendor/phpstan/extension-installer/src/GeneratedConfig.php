<?php declare(strict_types = 1);

namespace PHPStan\ExtensionInstaller;

/**
 * This class is generated by phpstan/extension-installer.
 * @internal
 */
final class GeneratedConfig
{

	public const EXTENSIONS = array (
  'phpstan/phpstan-phpunit' => 
  array (
    'install_path' => 'C:\\xampp\\phpMyAdmin\\vendor/phpstan/phpstan-phpunit',
    'relative_install_path' => '../../phpstan-phpunit',
    'extra' => 
    array (
      'includes' => 
      array (
        0 => 'extension.neon',
        1 => 'rules.neon',
      ),
    ),
    'version' => '1.3.3',
  ),
  'phpstan/phpstan-webmozart-assert' => 
  array (
    'install_path' => 'C:\\xampp\\phpMyAdmin\\vendor/phpstan/phpstan-webmozart-assert',
    'relative_install_path' => '../../phpstan-webmozart-assert',
    'extra' => 
    array (
      'includes' => 
      array (
        0 => 'extension.neon',
      ),
    ),
    'version' => '1.2.2',
  ),
);

	public const NOT_INSTALLED = array (
);

	private function __construct()
	{
	}

}
